"""ServisPlan: local shuttle dispatch prototype, Python standard library only."""
import argparse
import csv
import io
import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent
LOCAL_TZ = ZoneInfo('Europe/Istanbul')


class RuleError(ValueError):
    pass


def label(data, key, limit=100):
    value = data.get(key)
    if not isinstance(value, str) or not 1 <= len(value.strip()) <= limit:
        raise RuleError(f'{key}: 1–{limit} karakter girin.')
    return value.strip()


def integer(data, key, minimum=1, maximum=1000000):
    value = data.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise RuleError(f'{key}: tam sayı girin.')
    try:
        number = int(value)
    except (ValueError, TypeError):
        raise RuleError(f'{key}: tam sayı girin.')
    if not minimum <= number <= maximum:
        raise RuleError(f'{key}: {minimum}–{maximum} arası olmalı.')
    return number


def enum(data, key, choices):
    value = data.get(key)
    if value not in choices:
        raise RuleError(f'{key}: geçersiz seçenek.')
    return value


class Store:
    def __init__(self, path):
        self.path = str(path)
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with self.db() as db:
            db.executescript('''
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS vehicles (
              id INTEGER PRIMARY KEY, code TEXT UNIQUE NOT NULL, model TEXT NOT NULL,
              capacity INTEGER NOT NULL CHECK(capacity>0), status TEXT NOT NULL CHECK(status IN ('ready','maintenance')));
            CREATE TABLE IF NOT EXISTS drivers (
              id INTEGER PRIMARY KEY, name TEXT NOT NULL, code TEXT UNIQUE NOT NULL);
            CREATE TABLE IF NOT EXISTS routes (
              id INTEGER PRIMARY KEY, name TEXT NOT NULL, destination TEXT NOT NULL,
              duration INTEGER NOT NULL CHECK(duration>0));
            CREATE TABLE IF NOT EXISTS stops (
              id INTEGER PRIMARY KEY, route_id INTEGER NOT NULL REFERENCES routes(id),
              name TEXT NOT NULL, minute INTEGER NOT NULL CHECK(minute>=0), UNIQUE(route_id,minute));
            CREATE TABLE IF NOT EXISTS passengers (
              id INTEGER PRIMARY KEY, name TEXT NOT NULL, organization TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS assignments (
              passenger_id INTEGER PRIMARY KEY REFERENCES passengers(id), stop_id INTEGER NOT NULL REFERENCES stops(id));
            CREATE TABLE IF NOT EXISTS trips (
              id INTEGER PRIMARY KEY, route_id INTEGER NOT NULL REFERENCES routes(id),
              vehicle_id INTEGER NOT NULL REFERENCES vehicles(id), driver_id INTEGER NOT NULL REFERENCES drivers(id),
              departure TEXT NOT NULL, arrival TEXT NOT NULL,
              status TEXT NOT NULL CHECK(status IN ('planned','running','completed','cancelled')));
            CREATE TABLE IF NOT EXISTS manifest (
              trip_id INTEGER NOT NULL REFERENCES trips(id), passenger_id INTEGER NOT NULL REFERENCES passengers(id),
              name TEXT NOT NULL, organization TEXT NOT NULL, stop_name TEXT NOT NULL, minute INTEGER NOT NULL,
              attendance TEXT NOT NULL CHECK(attendance IN ('pending','boarded','absent')),
              PRIMARY KEY(trip_id,passenger_id));
            CREATE TABLE IF NOT EXISTS events (
              id INTEGER PRIMARY KEY, action TEXT NOT NULL, detail TEXT NOT NULL, created_at TEXT NOT NULL);
            ''')

    @contextmanager
    def db(self):
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute('PRAGMA foreign_keys=ON')
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def log(self, db, action, detail):
        db.execute('INSERT INTO events(action,detail,created_at) VALUES(?,?,?)',
                   (action, detail, datetime.now(LOCAL_TZ).isoformat(timespec='seconds')))

    def exists(self, db, table, key):
        # Table names are internal constants, never request input.
        row = db.execute(f'SELECT * FROM {table} WHERE id=?', (key,)).fetchone()
        if row is None:
            raise RuleError('İlgili kayıt bulunamadı.')
        return row

    def add_vehicle(self, data):
        values = (label(data,'code',20).upper(), label(data,'model'), integer(data,'capacity',1,100), enum(data,'status',['ready','maintenance']))
        with self.db() as db:
            try:
                key = db.execute('INSERT INTO vehicles(code,model,capacity,status) VALUES(?,?,?,?)', values).lastrowid
            except sqlite3.IntegrityError:
                raise RuleError('Bu araç kodu zaten kayıtlı.')
            self.log(db,'Araç eklendi',values[0])
        return {'id':key}

    def add_driver(self, data):
        with self.db() as db:
            try:
                key = db.execute('INSERT INTO drivers(name,code) VALUES(?,?)', (label(data,'name'), label(data,'code',20).upper())).lastrowid
            except sqlite3.IntegrityError:
                raise RuleError('Bu sürücü kodu zaten kayıtlı.')
            self.log(db,'Sürücü eklendi',label(data,'name'))
        return {'id':key}

    def add_route(self, data):
        with self.db() as db:
            key = db.execute('INSERT INTO routes(name,destination,duration) VALUES(?,?,?)',
                             (label(data,'name'), label(data,'destination'), integer(data,'duration',1,720))).lastrowid
            self.log(db,'Hat eklendi',label(data,'name'))
        return {'id':key}

    def add_stop(self, data):
        route_id, minute = integer(data,'route_id'), integer(data,'minute',0,719)
        with self.db() as db:
            route = self.exists(db,'routes',route_id)
            if minute >= route['duration']:
                raise RuleError('Durak dakikası toplam yolculuk süresinden küçük olmalı.')
            try:
                key = db.execute('INSERT INTO stops(route_id,name,minute) VALUES(?,?,?)',
                                 (route_id,label(data,'name'),minute)).lastrowid
            except sqlite3.IntegrityError:
                raise RuleError('Bu hatta aynı dakikada bir durak zaten var.')
            self.log(db,'Durak eklendi',label(data,'name'))
        return {'id':key}

    def add_passenger(self, data):
        with self.db() as db:
            key = db.execute('INSERT INTO passengers(name,organization) VALUES(?,?)',
                             (label(data,'name'),label(data,'organization'))).lastrowid
            self.log(db,'Yolcu eklendi',label(data,'name'))
        return {'id':key}

    def assign(self, data):
        passenger_id, stop_id = integer(data,'passenger_id'), integer(data,'stop_id')
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            passenger = self.exists(db,'passengers',passenger_id)
            stop = self.exists(db,'stops',stop_id)
            db.execute('INSERT INTO assignments(passenger_id,stop_id) VALUES(?,?) ON CONFLICT(passenger_id) DO UPDATE SET stop_id=excluded.stop_id', (passenger_id,stop_id))
            self.log(db,'Yolcu atandı',f"{passenger['name']} → {stop['name']}")
        return {'passenger_id':passenger_id}

    def unassign(self, data):
        passenger_id = integer(data,'passenger_id')
        with self.db() as db:
            passenger = self.exists(db,'passengers',passenger_id)
            db.execute('DELETE FROM assignments WHERE passenger_id=?',(passenger_id,))
            self.log(db,'Atama kaldırıldı',passenger['name'])
        return {'passenger_id':passenger_id}

    def add_trip(self, data):
        route_id, vehicle_id, driver_id = (integer(data,key) for key in ['route_id','vehicle_id','driver_id'])
        try:
            departure = datetime.strptime(label(data,'date',10)+' '+label(data,'time',5), '%Y-%m-%d %H:%M')
        except ValueError:
            raise RuleError('Geçerli tarih ve saat girin.')
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            route = self.exists(db,'routes',route_id)
            vehicle = self.exists(db,'vehicles',vehicle_id)
            self.exists(db,'drivers',driver_id)
            if vehicle['status'] != 'ready':
                raise RuleError('Bakımdaki araç sefere atanamaz.')
            if not db.execute('SELECT 1 FROM stops WHERE route_id=?',(route_id,)).fetchone():
                raise RuleError('Seferden önce hatta en az bir durak ekleyin.')
            people = db.execute('SELECT p.*,s.name AS stop_name,s.minute FROM assignments a JOIN passengers p ON p.id=a.passenger_id JOIN stops s ON s.id=a.stop_id WHERE s.route_id=? ORDER BY s.minute,p.id',(route_id,)).fetchall()
            if len(people) > vehicle['capacity']:
                raise RuleError(f"Araç kapasitesi {vehicle['capacity']}; hatta {len(people)} yolcu atanmış.")
            arrival = departure + timedelta(minutes=route['duration'])
            start, end = departure.isoformat(timespec='minutes'), arrival.isoformat(timespec='minutes')
            conflicts = db.execute("SELECT id FROM trips WHERE status IN ('planned','running') AND departure<? AND arrival>? AND (vehicle_id=? OR driver_id=?)", (end,start,vehicle_id,driver_id)).fetchall()
            if conflicts:
                raise RuleError('Bu zaman aralığında araç veya sürücü başka bir sefere atanmış.')
            # A passenger cannot be booked onto overlapping active trips either.
            for person in people:
                if db.execute("SELECT 1 FROM manifest m JOIN trips t ON t.id=m.trip_id WHERE m.passenger_id=? AND t.status IN ('planned','running') AND t.departure<? AND t.arrival>?",(person['id'],end,start)).fetchone():
                    raise RuleError('Yolcu listesinde aynı saatte başka sefere atanmış kişi var.')
            key = db.execute("INSERT INTO trips(route_id,vehicle_id,driver_id,departure,arrival,status) VALUES(?,?,?,?,?,'planned')", (route_id,vehicle_id,driver_id,start,end)).lastrowid
            db.executemany("INSERT INTO manifest(trip_id,passenger_id,name,organization,stop_name,minute,attendance) VALUES(?,?,?,?,?,?,'pending')", [(key,p['id'],p['name'],p['organization'],p['stop_name'],p['minute']) for p in people])
            self.log(db,'Sefer planlandı',f"#{key} · {route['name']} · {start}")
        return {'id':key}

    def transition(self, data):
        trip_id = integer(data,'trip_id')
        target = enum(data,'status',['running','completed','cancelled'])
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            trip = self.exists(db,'trips',trip_id)
            if target not in {'planned':['running','cancelled'], 'running':['completed'], 'completed':[], 'cancelled':[]}[trip['status']]:
                raise RuleError('Bu sefer için durum geçişi geçerli değil.')
            if target == 'running':
                # Even non-overlapping scheduled trips cannot use an already running resource.
                if db.execute("SELECT 1 FROM trips WHERE id!=? AND status='running' AND (vehicle_id=? OR driver_id=?)", (trip_id,trip['vehicle_id'],trip['driver_id'])).fetchone():
                    raise RuleError('Araç veya sürücünün devam eden seferini önce tamamlayın.')
            if target == 'completed' and db.execute("SELECT 1 FROM manifest WHERE trip_id=? AND attendance='pending'",(trip_id,)).fetchone():
                raise RuleError('Seferi tamamlamadan önce tüm yolcuların yoklamasını kaydedin.')
            db.execute('UPDATE trips SET status=? WHERE id=?',(target,trip_id))
            self.log(db,{'running':'Sefer başladı','completed':'Sefer tamamlandı','cancelled':'Sefer iptal edildi'}[target],f'Sefer #{trip_id}')
        return {'id':trip_id,'status':target}

    def attendance(self, data):
        trip_id, passenger_id = integer(data,'trip_id'), integer(data,'passenger_id')
        value = enum(data,'attendance',['boarded','absent','pending'])
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            trip = self.exists(db,'trips',trip_id)
            if trip['status'] != 'running':
                raise RuleError('Yoklama yalnızca devam eden seferde değiştirilebilir.')
            if not db.execute('SELECT 1 FROM manifest WHERE trip_id=? AND passenger_id=?',(trip_id,passenger_id)).fetchone():
                raise RuleError('Yolcu bu seferin listesinde bulunamadı.')
            db.execute('UPDATE manifest SET attendance=? WHERE trip_id=? AND passenger_id=?',(value,trip_id,passenger_id))
            attendance_label = {'boarded':'Bindi','absent':'Gelmedi','pending':'Bekliyor'}[value]
            self.log(db,'Yoklama güncellendi',f'Sefer #{trip_id} · Yolcu #{passenger_id} · {attendance_label}')
        return {'trip_id':trip_id,'passenger_id':passenger_id}

    def state(self):
        with self.db() as db:
            # One read transaction keeps all dashboard collections consistent.
            db.execute('BEGIN')
            queries = {
                'vehicles':'SELECT * FROM vehicles ORDER BY id',
                'drivers':'SELECT * FROM drivers ORDER BY id',
                'routes':'SELECT r.*, (SELECT COUNT(*) FROM assignments a JOIN stops s ON s.id=a.stop_id WHERE s.route_id=r.id) AS passengers FROM routes r ORDER BY id',
                'stops':'SELECT * FROM stops ORDER BY route_id,minute',
                'passengers':'SELECT p.*,a.stop_id,s.route_id,s.name AS stop_name FROM passengers p LEFT JOIN assignments a ON a.passenger_id=p.id LEFT JOIN stops s ON s.id=a.stop_id ORDER BY p.id',
                'trips':"SELECT t.*,r.name AS route_name,r.destination,v.code AS vehicle_code,v.capacity,d.name AS driver_name,(SELECT COUNT(*) FROM manifest m WHERE m.trip_id=t.id) AS booked,(SELECT COUNT(*) FROM manifest m WHERE m.trip_id=t.id AND attendance='boarded') AS boarded FROM trips t JOIN routes r ON r.id=t.route_id JOIN vehicles v ON v.id=t.vehicle_id JOIN drivers d ON d.id=t.driver_id ORDER BY t.departure DESC,t.id DESC",
                'manifest':'SELECT * FROM manifest ORDER BY trip_id,minute,passenger_id',
                'events':'SELECT * FROM events ORDER BY id DESC LIMIT 25'}
            return {key:[dict(row) for row in db.execute(query)] for key,query in queries.items()}

    def seed(self):
        with self.db() as db:
            if db.execute('SELECT COUNT(*) FROM vehicles').fetchone()[0]:
                return
        for code,model,capacity,status in [('DEMO-01','16 kişilik minibüs',16,'ready'),('DEMO-02','19 kişilik minibüs',19,'ready'),('DEMO-03','27 kişilik midibüs',27,'ready'),('DEMO-04','16 kişilik minibüs',16,'maintenance')]:
            self.add_vehicle(dict(code=code,model=model,capacity=capacity,status=status))
        for i,name in enumerate(['Deniz Örnek','Ekin Demo','Arda Temsili'],1):
            self.add_driver(dict(name=name,code=f'SRC-{i:03}'))
        for name,destination,duration in [('Kuzey Hattı','Teknoloji Kampüsü',55),('Sahil Hattı','Merkez Ofis',45),('Batı Hattı','Üretim Kampüsü',60)]:
            self.add_route(dict(name=name,destination=destination,duration=duration))
        for rid,stops in [(1,[('Kent Meydanı',0),('Çınar Mahallesi',12),('Park Kavşağı',28),('Kampüs Girişi',46)]),(2,[('Sahil Meydanı',0),('İskele Durağı',14),('Merkez Bulvar',31)]),(3,[('Batı Terminali',0),('Sanayi Kavşağı',20),('Fabrika Yolu',42)])]:
            for name,minute in stops:
                self.add_stop(dict(route_id=rid,name=name,minute=minute))
        for i in range(1,33):
            self.add_passenger(dict(name=f'Örnek Yolcu {i:02}',organization='Demo Teknoloji' if i<21 else 'Demo Üretim'))
            self.assign(dict(passenger_id=i,stop_id=((i-1)%4)+1 if i<=14 else (5+(i%3) if i<=24 else 8+(i%3))))
        today = datetime.now(LOCAL_TZ).date().isoformat()
        for rid,vid,did,time in [(1,1,1,'07:15'),(2,2,2,'07:30'),(3,3,3,'07:00'),(1,1,1,'17:30')]:
            self.add_trip(dict(route_id=rid,vehicle_id=vid,driver_id=did,date=today,time=time))


def handler(store):
    class Handler(BaseHTTPRequestHandler):
        def respond(self, code, body, mime='application/json; charset=utf-8'):
            if isinstance(body,(dict,list)):
                body = json.dumps(body,ensure_ascii=False).encode()
            elif isinstance(body,str):
                body = body.encode()
            self.send_response(code)
            self.send_header('Content-Type',mime)
            self.send_header('Content-Length',str(len(body)))
            self.send_header('X-Content-Type-Options','nosniff')
            self.send_header('Cache-Control','no-store')
            self.send_header('Content-Security-Policy',"default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; frame-ancestors 'none'; base-uri 'none'")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            path = urlsplit(self.path).path
            if path == '/api/state':
                return self.respond(200,store.state())
            if path == '/api/report.csv':
                output = io.StringIO()
                writer = csv.writer(output)
                writer.writerow(['Sefer','Hat','Kalkış','Araç','Kapasite','Planlanan yolcu','Binen yolcu','Durum'])
                for trip in store.state()['trips']:
                    # Prevent spreadsheet formula injection from user-authored route names.
                    def safe(value):
                        if isinstance(value,str) and (value.lstrip().startswith(('=','+','-','@')) or value.startswith(('\t','\r','\n'))):
                            return "'"+value
                        return value
                    writer.writerow([safe(value) for value in [trip['id'],trip['route_name'],trip['departure'],trip['vehicle_code'],trip['capacity'],trip['booked'],trip['boarded'],trip['status']]])
                return self.respond(200,'\ufeff'+output.getvalue(),'text/csv; charset=utf-8')
            files = {'/':('index.html','text/html'),'/app.js':('app.js','text/javascript'),'/style.css':('style.css','text/css')}
            if path not in files:
                return self.respond(404,{'error':'Bulunamadı.'})
            name,mime = files[path]
            self.respond(200,(ROOT/'static'/name).read_bytes(),mime+'; charset=utf-8')

        def do_POST(self):
            host, origin = self.headers.get('Host',''), self.headers.get('Origin')
            if host not in [f'localhost:{self.server.server_port}',f'127.0.0.1:{self.server.server_port}'] or (origin and origin != f'http://{host}'):
                return self.respond(403,{'error':'Kaynak adresi reddedildi.'})
            if self.headers.get('Content-Type','').split(';')[0] != 'application/json':
                return self.respond(415,{'error':'JSON gerekli.'})
            routes = {'/api/vehicles':store.add_vehicle,'/api/drivers':store.add_driver,'/api/routes':store.add_route,
                      '/api/stops':store.add_stop,'/api/passengers':store.add_passenger,'/api/assign':store.assign,
                      '/api/unassign':store.unassign,'/api/trips':store.add_trip,'/api/trips/status':store.transition,
                      '/api/attendance':store.attendance}
            path = urlsplit(self.path).path
            if path not in routes:
                return self.respond(404,{'error':'Bulunamadı.'})
            try:
                length = int(self.headers.get('Content-Length','0'))
                if not 0 < length <= 16384:
                    raise RuleError('İstek boyutu geçersiz.')
                data = json.loads(self.rfile.read(length))
                if not isinstance(data,dict):
                    raise RuleError('JSON nesnesi gerekli.')
                result = routes[path](data)
                self.respond(201,result)
            except (ValueError,UnicodeError) as exc:
                self.respond(400,{'error':str(exc) if isinstance(exc,RuleError) else 'Geçersiz istek.'})
            except sqlite3.Error:
                self.respond(503,{'error':'Veritabanı işlemi tamamlanamadı.'})
    return Handler


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='ServisPlan yerel demo sunucusu')
    parser.add_argument('--port',type=int,default=8001)
    parser.add_argument('--db',default=str(ROOT/'data'/'servisplan.db'))
    parser.add_argument('--empty',action='store_true')
    args = parser.parse_args()
    store = Store(args.db)
    if not args.empty:
        store.seed()
    server = ThreadingHTTPServer(('127.0.0.1',args.port),handler(store))
    print(f'ServisPlan hazır: http://127.0.0.1:{args.port}',flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
