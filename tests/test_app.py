import csv
import io
import json
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
from pathlib import Path

from app import RuleError, Store, handler


class Fixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(Path(self.temp.name)/'test.db')
        for code,status in [('TEST-01','ready'),('TEST-02','ready'),('TEST-03','maintenance')]:
            self.store.add_vehicle(dict(code=code,model='Demo',capacity=2,status=status))
        for i in range(1,3):
            self.store.add_driver(dict(name=f'Sürücü {i}',code=f'S{i}'))
        for i in range(1,3):
            self.store.add_route(dict(name=f'Hat {i}',destination='Kampüs',duration=40))
            self.store.add_stop(dict(route_id=i,name=f'Durak {i}',minute=0))
        for i in range(1,3):
            self.store.add_passenger(dict(name=f'Yolcu {i}',organization='Demo'))
            self.store.assign(dict(passenger_id=i,stop_id=1))

    def trip(self,**changes):
        data=dict(route_id=1,vehicle_id=1,driver_id=1,date='2030-01-10',time='07:00')
        data.update(changes)
        return self.store.add_trip(data)['id']


class DomainTests(Fixture):
    def test_seed_is_idempotent(self):
        fresh=Store(Path(self.temp.name)/'seed.db')
        fresh.seed();fresh.seed()
        self.assertEqual(len(fresh.state()['trips']),4)
        self.assertEqual(len(fresh.state()['passengers']),32)

    def test_capacity_limit(self):
        self.store.add_passenger(dict(name='Fazla Yolcu',organization='Demo'))
        self.store.assign(dict(passenger_id=3,stop_id=1))
        with self.assertRaisesRegex(RuleError,'kapasitesi'):
            self.trip()
        self.assertEqual(self.store.state()['trips'],[])

    def test_rejects_vehicle_overlap(self):
        self.trip()
        with self.assertRaisesRegex(RuleError,'araç veya sürücü'):
            self.trip(driver_id=2,time='07:20',route_id=2)

    def test_rejects_driver_overlap(self):
        self.trip()
        with self.assertRaisesRegex(RuleError,'araç veya sürücü'):
            self.trip(vehicle_id=2,time='07:20',route_id=2)

    def test_rejects_passenger_overlap(self):
        self.trip()
        with self.assertRaisesRegex(RuleError,'Yolcu listesinde'):
            self.trip(vehicle_id=2,driver_id=2,time='07:20')

    def test_adjacent_times_are_allowed(self):
        self.trip()
        self.trip(time='07:40')
        self.assertEqual(len(self.store.state()['trips']),2)

    def test_overnight_overlap(self):
        self.trip(time='23:45')
        with self.assertRaises(RuleError):
            self.trip(date='2030-01-11',time='00:05')

    def test_cancelled_trip_releases_resources(self):
        key=self.trip()
        self.store.transition(dict(trip_id=key,status='cancelled'))
        self.trip()
        with self.assertRaises(RuleError):
            self.store.transition(dict(trip_id=key,status='running'))

    def test_maintenance_vehicle_is_rejected(self):
        with self.assertRaisesRegex(RuleError,'Bakımdaki'):
            self.trip(vehicle_id=3)

    def test_manifest_is_snapshot(self):
        key=self.trip()
        self.store.assign(dict(passenger_id=1,stop_id=2))
        self.store.unassign(dict(passenger_id=2))
        state=self.store.state()
        manifest=[m for m in state['manifest'] if m['trip_id']==key]
        self.assertEqual(len(manifest),2)
        self.assertTrue(all(m['stop_name']=='Durak 1' for m in manifest))
        self.assertEqual(next(r for r in state['routes'] if r['id']==1)['passengers'],0)

    def test_assignment_replaces_previous(self):
        self.store.assign(dict(passenger_id=1,stop_id=2))
        p=self.store.state()['passengers'][0]
        self.assertEqual(p['route_id'],2)

    def test_complete_requires_attendance(self):
        key=self.trip()
        self.store.transition(dict(trip_id=key,status='running'))
        with self.assertRaisesRegex(RuleError,'yoklamasını'):
            self.store.transition(dict(trip_id=key,status='completed'))
        self.store.attendance(dict(trip_id=key,passenger_id=1,attendance='boarded'))
        self.store.attendance(dict(trip_id=key,passenger_id=2,attendance='absent'))
        self.store.transition(dict(trip_id=key,status='completed'))
        trip=self.store.state()['trips'][0]
        self.assertEqual(trip['boarded'],1)
        self.assertEqual(trip['status'],'completed')
        with self.assertRaises(RuleError):
            self.store.attendance(dict(trip_id=key,passenger_id=1,attendance='absent'))

    def test_attendance_only_when_running(self):
        key=self.trip()
        with self.assertRaises(RuleError):
            self.store.attendance(dict(trip_id=key,passenger_id=1,attendance='boarded'))
        self.store.transition(dict(trip_id=key,status='running'))
        with self.assertRaises(RuleError):
            self.store.attendance(dict(trip_id=key,passenger_id=999,attendance='boarded'))

    def test_started_resource_blocks_another_start(self):
        first=self.trip();second=self.trip(time='09:00')
        self.store.transition(dict(trip_id=first,status='running'))
        with self.assertRaisesRegex(RuleError,'devam eden'):
            self.store.transition(dict(trip_id=second,status='running'))

    def test_concurrent_scheduling(self):
        def schedule(_):
            try:
                self.trip()
                return True
            except RuleError:
                return False
        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertEqual(sorted(pool.map(schedule,range(2))),[False,True])
        self.assertEqual(len(self.store.state()['trips']),1)

    def test_invalid_numeric_values(self):
        for value in [True,False,0,-1,'1.5','NaN',[],None,101]:
            with self.subTest(value=value),self.assertRaises(RuleError):
                self.store.add_vehicle(dict(code='invalid',model='Demo',capacity=value,status='ready'))

    def test_stop_bounds_and_duplicate(self):
        for minute in [0,40,-1]:
            with self.subTest(minute=minute),self.assertRaises(RuleError):
                self.store.add_stop(dict(route_id=1,name='Test',minute=minute))

    def test_no_stops_no_trip(self):
        rid=self.store.add_route(dict(name='Boş hat',destination='Merkez',duration=30))['id']
        with self.assertRaisesRegex(RuleError,'en az bir durak'):
            self.trip(route_id=rid)

    def test_unknown_records_and_invalid_date(self):
        with self.assertRaises(RuleError):
            self.store.assign(dict(passenger_id=999,stop_id=1))
        with self.assertRaises(RuleError):
            self.trip(date='2030-02-30')

    def test_persistence(self):
        self.trip()
        self.assertEqual(len(Store(self.store.path).state()['trips']),1)


class HTTPTests(Fixture):
    def setUp(self):
        super().setUp()
        self.server=ThreadingHTTPServer(('127.0.0.1',0),handler(self.store))
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True)
        self.thread.start()
        self.addCleanup(self.close_server)

    def close_server(self):
        self.server.shutdown();self.server.server_close();self.thread.join()

    def call(self,path,method='GET',data=None,extra=None):
        conn=HTTPConnection('127.0.0.1',self.server.server_port)
        headers={'Content-Type':'application/json'};headers.update(extra or {})
        conn.request(method,path,json.dumps(data) if data is not None else None,headers)
        response=conn.getresponse();body=response.read();code=response.status;conn.close()
        return code,body

    def test_files_and_validation(self):
        for path in ['/','/app.js','/style.css','/api/state','/api/report.csv']:
            self.assertEqual(self.call(path)[0],200)
        self.assertEqual(self.call('/../app.py')[0],404)
        self.assertEqual(self.call('/api/vehicles','POST',[])[0],400)
        self.assertEqual(self.call('/api/vehicles','POST',{}, {'Origin':'https://evil.example'})[0],403)
        self.assertEqual(self.call('/api/vehicles','POST',{}, {'Content-Type':'text/plain'})[0],415)

    def test_api_lifecycle(self):
        code,body=self.call('/api/trips','POST',dict(route_id=1,vehicle_id=1,driver_id=1,date='2030-01-10',time='08:00'))
        self.assertEqual(code,201);key=json.loads(body)['id']
        self.assertEqual(self.call('/api/trips/status','POST',dict(trip_id=key,status='running'))[0],201)
        for passenger in [1,2]:
            self.assertEqual(self.call('/api/attendance','POST',dict(trip_id=key,passenger_id=passenger,attendance='boarded'))[0],201)
        self.assertEqual(self.call('/api/trips/status','POST',dict(trip_id=key,status='completed'))[0],201)
        self.assertIn('completed',self.call('/api/report.csv')[1].decode('utf-8-sig'))

    def test_csv_formula_neutralization(self):
        with self.store.db() as db:
            db.execute("UPDATE routes SET name='=1+1' WHERE id=1")
            db.execute("UPDATE vehicles SET code='=1+1' WHERE id=1")
        self.trip()
        rows=list(csv.reader(io.StringIO(self.call('/api/report.csv')[1].decode('utf-8-sig'))))
        self.assertEqual(rows[1][1],"'=1+1")
        self.assertEqual(rows[1][3],"'=1+1")


if __name__=='__main__':
    unittest.main()
