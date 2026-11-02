'use strict';
const $=s=>document.querySelector(s);
const esc=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const fmt=n=>new Intl.NumberFormat('tr-TR',{maximumFractionDigits:1}).format(n);
const dateParts=Object.fromEntries(new Intl.DateTimeFormat('en',{timeZone:'Europe/Istanbul',year:'numeric',month:'2-digit',day:'2-digit'}).formatToParts(new Date()).map(x=>[x.type,x.value]));
const today=`${dateParts.year}-${dateParts.month}-${dateParts.day}`;
const shortDate=iso=>new Intl.DateTimeFormat('tr-TR',{day:'numeric',month:'short',timeZone:'Europe/Istanbul'}).format(new Date(`${iso.slice(0,10)}T12:00:00+03:00`));
const statusLabels={planned:'Planlandı',running:'Devam ediyor',completed:'Tamamlandı',cancelled:'İptal edildi',ready:'Sefere hazır',maintenance:'Bakımda'};
const attendanceLabels={pending:'Bekliyor',boarded:'Bindi',absent:'Gelmedi'};
const badge=status=>`<span class="badge ${esc(status)}">${esc(statusLabels[status]||attendanceLabels[status])}</span>`;
const empty=message=>`<div class="empty">${esc(message)}</div>`;
let state,view='dashboard',tripFilter='all',tripDate=today,search='',selectedRoute=null,manifestId=null,timer;
const pages={dashboard:['Operasyon özeti','Operasyonun kontrolü sende.','Seferleri planla, kapasiteyi dengede tut, yolcuları organize et.'],trips:['Seferler','Her sefer için net bir plan.','Araç, sürücü ve yolcu listelerini tek bir operasyon akışında yönet.'],routes:['Hatlar ve duraklar','Doğru durak. Düzenli yolculuk.','Hatlarını oluştur, durak sırasını ve tahmini ulaşım sürelerini belirle.'],fleet:['Araçlar ve sürücüler','Yola hazır bir ekip.','Araç kapasitesini ve sürücü havuzunu aynı çalışma alanında takip et.'],passengers:['Yolcular','Her yolcunun bir yeri var.','Yolcuları duraklara ata; yeni seferlerin listeleri bu atamalardan oluşur.'],reports:['Raporlar','Operasyonu sayılarla gör.','Planlanan kapasiteyi ve tamamlanan seferlerin gerçekleşen doluluğunu incele.']};
async function api(path,data){const r=await fetch(path,data===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)});const result=await r.json();if(!r.ok)throw new Error(result.error||'İşlem tamamlanamadı.');return result;}
async function refresh(){state=await api('/api/state');render();}
function notify(message){$('#toast').textContent=message;$('#toast').hidden=false;clearTimeout(timer);timer=setTimeout(()=>$('#toast').hidden=true,5000);}
function go(next){view=next;render();}
function occupancy(booked,capacity){const ratio=capacity?booked/capacity*100:0;return `<div class="capacity ${ratio>=85?'high':''}"><span>${booked} / ${capacity}</span><progress value="${booked}" max="${capacity}" aria-label="${booked} yolcu, ${capacity} kapasite"></progress></div>`;}
function stats(){
  const trips=state.trips.filter(t=>t.departure.startsWith(today)&&t.status!=='cancelled');
  const ready=state.vehicles.filter(v=>v.status==='ready').length;
  const totalCapacity=trips.reduce((n,t)=>n+t.capacity,0), booked=trips.reduce((n,t)=>n+t.booked,0);
  const values=[['Bugünün seferleri',trips.length,`<b>${trips.filter(t=>t.status==='running').length} devam ediyor</b> · ${trips.filter(t=>t.status==='planned').length} planlandı`,'⇄'],['Sefere hazır araç',`${ready} <small>/ ${state.vehicles.length}</small>`,`${state.vehicles.length-ready} araç bakımda`,'▱'],['Kayıtlı yolcu',state.passengers.length,`<b>${state.passengers.filter(p=>p.stop_id).length} yolcu</b> bir durağa atanmış`,'◎'],['Planlanan doluluk',`%${fmt(totalCapacity?booked/totalCapacity*100:0)}`,`Bugünkü ${trips.length} sefer · iptaller hariç`,'▥']];
  $('#stats').innerHTML=values.map(([label,value,caption,icon])=>`<div class="stat"><div class="stat-heading">${label}<span class="stat-icon">${icon}</span></div><div class="stat-value">${value}</div><div class="stat-caption">${caption}</div></div>`).join('');
}
function render(){
  const [breadcrumb,title,subtitle]=pages[view];$('#breadcrumb').textContent=breadcrumb;$('#title').textContent=title;$('#subtitle').textContent=subtitle;
  $('#today').textContent=new Intl.DateTimeFormat('tr-TR',{day:'numeric',month:'long',year:'numeric',timeZone:'Europe/Istanbul'}).format(new Date());
  document.querySelectorAll('[data-view]').forEach(b=>{b.classList.toggle('active',b.dataset.view===view);b.setAttribute('aria-current',b.dataset.view===view?'page':'false');});
  $('#trip-count').textContent=state.trips.filter(t=>t.status==='planned').length;
  $('#main-action').textContent={routes:'＋ Hat oluştur',fleet:'＋ Araç ekle',passengers:'＋ Yolcu ekle'}[view]||'＋ Sefer planla';
  stats();({dashboard:renderDashboard,trips:renderTrips,routes:renderRoutes,fleet:renderFleet,passengers:renderPassengers,reports:renderReports})[view]();
}
function filterTabs(){return `<div class="tabs" role="group" aria-label="Sefer durumu">${[['all','Tüm seferler'],['planned','Planlanan'],['running','Devam eden'],['completed','Tamamlanan'],['cancelled','İptal']].map(([key,label])=>`<button data-filter="${key}" class="${tripFilter===key?'selected':''}" aria-pressed="${tripFilter===key}">${label}</button>`).join('')}</div>`;}
function tripTable(trips,compact=false){
  if(!trips.length)return empty('Bu tarih ve duruma uygun sefer yok. Yeni bir sefer planlayabilirsin.');
  return `<div class="table-wrap"><table><thead><tr><th>HAT / ARAÇ</th><th>KALKIŞ</th><th>${compact?'DOLULUK':'SÜRÜCÜ / DOLULUK'}</th><th>DURUM</th><th>İŞLEMLER</th></tr></thead><tbody>${trips.map(t=>`<tr><td><div class="cell-main">${esc(t.route_name)}</div><div class="cell-sub">#${t.id} · ${esc(t.vehicle_code)} → ${esc(t.destination)}</div></td><td><div class="time">${esc(t.departure.slice(11))}</div><div class="cell-sub">${shortDate(t.departure)} · Varış ${esc(t.arrival.slice(11))}${t.arrival.slice(0,10)!==t.departure.slice(0,10)?' (+1 gün)':''}</div></td><td>${compact?'':`<div class="cell-sub">${esc(t.driver_name)}</div><br>`}${occupancy(t.booked,t.capacity)}</td><td>${badge(t.status)}</td><td><div class="row-actions"><button class="secondary" data-manifest="${t.id}">Yolcu listesi</button>${t.status==='planned'?`<button class="text-button" data-transition="${t.id}:running">Başlat ↗</button>`:t.status==='running'?`<button class="text-button" data-transition="${t.id}:completed">Tamamla ✓</button>`:''}</div>${!compact&&t.status==='planned'?`<br><button class="text-button" data-transition="${t.id}:cancelled">Seferi iptal et</button>`:''}</td></tr>`).join('')}</tbody></table></div>`;
}
function diagram(route){
  const stops=state.stops.filter(s=>s.route_id===route.id);
  return `<ol class="route-diagram">${stops.map(s=>`<li>${esc(s.name)}<b>+${s.minute} dk</b><small>${state.passengers.filter(p=>p.stop_id===s.id).length} atanmış yolcu</small></li>`).join('')}<li>${esc(route.destination)}<b>+${route.duration} dk</b><small>Varış noktası</small></li></ol>`;
}
function renderDashboard(){
  const trips=state.trips.filter(t=>t.departure.startsWith(today)&&(tripFilter==='all'||t.status===tripFilter)).sort((a,b)=>a.departure.localeCompare(b.departure));
  const route=state.routes[0];
  $('#content').innerHTML=`<div class="dashboard-grid"><div><div class="panel"><div class="panel-heading"><div><h2>Bugünün sefer akışı</h2><p>Planlanan saatler · ${shortDate(today)}</p></div><button class="text-button" data-go="trips">Tüm seferler ↗</button></div>${filterTabs()}${tripTable(trips,true)}</div><div class="bottom-grid"><section class="panel"><div class="panel-heading"><h2>Filo durumu</h2><button class="text-button" data-go="fleet">Filoyu gör ↗</button></div><div class="fleet-bars">${state.vehicles.slice(0,4).map(v=>`<div class="fleet-row"><span class="vehicle-icon">▱</span><div><b>${esc(v.code)}</b><small>${esc(v.model)} · ${v.capacity} koltuk</small></div>${badge(v.status)}</div>`).join('')||empty('Henüz araç eklenmedi.')}</div></section><section class="panel"><div class="panel-heading"><h2>Son hareketler</h2><small>İşlem günlüğü</small></div><div class="activity">${state.events.slice(0,4).map(e=>`<div class="activity-row"><span class="activity-dot">↗</span><div><strong>${esc(e.action)}</strong><p>${esc(e.detail)}</p><time>${esc(e.created_at.slice(11,16))}</time></div></div>`).join('')||empty('Henüz hareket yok.')}</div></section></div></div><aside class="dashboard-aside">${route?`<div class="route-panel"><p class="eyebrow">HAT GÖRÜNÜMÜ · ŞEMATİK</p><h2>${esc(route.name)}</h2><p class="subtitle">${esc(route.destination)}<br>Durak sırası ve tahmini süreler</p>${diagram(route)}<div class="route-panel-bottom"><span>${state.stops.filter(s=>s.route_id===route.id).length} durak</span><span>${route.passengers} yolcu · ${route.duration} dk</span></div></div>`:''}<div class="notice"><strong>Planlama kontrolü aktif</strong>Kapasite, saat ve araç/sürücü çakışmaları sefer kaydedilirken kontrol edilir. Görünüm canlı GPS verisi içermez.</div></aside></div>`;
}
function renderTrips(){
  $('#content').innerHTML=`<div class="section-toolbar"><h2>Sefer planı</h2><div class="tools"><input id="trip-date" type="date" aria-label="Sefer tarihi" value="${tripDate}"><button class="secondary" data-all-dates>Tüm tarihler</button></div></div><div class="panel">${filterTabs()}<div id="trip-table"></div></div>`;
  $('#trip-date').addEventListener('change',e=>{tripDate=e.target.value;drawTrips();});drawTrips();
}
function drawTrips(){const trips=state.trips.filter(t=>(!tripDate||t.departure.startsWith(tripDate))&&(tripFilter==='all'||t.status===tripFilter));$('#trip-table').innerHTML=tripTable(trips);}
function renderRoutes(){
  $('#content').innerHTML=`<div class="hint">Her durak, kalkıştan itibaren dakika cinsinden sıralanır. Çizim şematiktir; yol ağına göre rota optimizasyonu veya gerçek yolculuk süresi hesabı yapılmaz.</div><div class="record-grid">${state.routes.map(r=>`<article class="record"><span class="pill">HAT ${String(r.id).padStart(2,'0')}</span><span class="pill">${r.passengers} atanmış yolcu</span><h3>${esc(r.name)}</h3><p>Varış: ${esc(r.destination)}<br>Planlanan toplam süre: ${r.duration} dakika</p>${diagram(r)}<div class="record-actions"><button class="secondary" data-stop="${r.id}">＋ Durak ekle</button><button class="text-button" data-route-trip="${r.id}">Sefer planla ↗</button></div></article>`).join('')||empty('İlk hattını oluşturarak başla.')}</div>`;
}
function renderFleet(){
  $('#content').innerHTML=`<div class="section-toolbar"><h2>Araç havuzu</h2><button class="secondary" data-open="drivers">＋ Sürücü ekle</button></div><div class="record-grid">${state.vehicles.map(v=>`<article class="record">${badge(v.status)}<h3>${esc(v.code)}</h3><p>${esc(v.model)}</p><div class="report-number">${v.capacity} <small class="muted">koltuk</small></div><p>${state.trips.filter(t=>t.vehicle_id===v.id&&t.status==='planned').length} planlanmış sefer</p></article>`).join('')||empty('Henüz araç yok.')}</div><br><div class="panel"><div class="panel-heading"><h2>Sürücü havuzu</h2><small>${state.drivers.length} sürücü</small></div><div class="table-wrap"><table><thead><tr><th>KOD</th><th>SÜRÜCÜ</th><th>PLANLANAN SEFER</th><th>DEVAM EDEN SEFER</th></tr></thead><tbody>${state.drivers.map(d=>`<tr><td>${esc(d.code)}</td><td>${esc(d.name)}</td><td>${state.trips.filter(t=>t.driver_id===d.id&&t.status==='planned').length}</td><td>${state.trips.filter(t=>t.driver_id===d.id&&t.status==='running').length}</td></tr>`).join('')}</tbody></table></div></div>`;
}
function renderPassengers(){
  $('#content').innerHTML=`<div class="hint">Bir yolcu bir durağa atanır. Sefer planlandığı anda yolcu listesi kopyalanır; atama değişiklikleri yalnızca bundan sonra oluşturulacak seferlere yansır.</div><div class="section-toolbar"><h2>Yolcu rehberi</h2><div class="tools"><input id="passenger-search" type="search" placeholder="Yolcu veya kurum ara…" aria-label="Yolcu ara" value="${esc(search)}"></div></div><div class="panel table-wrap"><table><thead><tr><th>YOLCU</th><th>KURUM</th><th>HAT / DURAK</th><th>İŞLEMLER</th></tr></thead><tbody id="passenger-rows"></tbody></table></div>`;
  $('#passenger-search').addEventListener('input',e=>{search=e.target.value;drawPassengers();});drawPassengers();
}
function drawPassengers(){
  const people=state.passengers.filter(p=>`${p.name} ${p.organization}`.toLocaleLowerCase('tr').includes(search.toLocaleLowerCase('tr')));
  $('#passenger-rows').innerHTML=people.map(p=>`<tr><td><div class="cell-main">${esc(p.name)}</div><div class="cell-sub">YLC-${String(p.id).padStart(3,'0')}</div></td><td>${esc(p.organization)}</td><td>${p.stop_id?`<div class="cell-main">${esc(state.routes.find(r=>r.id===p.route_id)?.name)}</div><div class="cell-sub">${esc(p.stop_name)}</div>`:'<span class="pill">Atama bekliyor</span>'}</td><td><div class="row-actions"><button class="secondary" data-assign="${p.id}">${p.stop_id?'Durağı değiştir':'Durağa ata'}</button>${p.stop_id?`<button class="text-button" data-unassign="${p.id}">Atamayı kaldır</button>`:''}</div></td></tr>`).join('')||'<tr><td colspan="4">Eşleşen yolcu bulunamadı.</td></tr>';
}
function renderReports(){
  const complete=state.trips.filter(t=>t.status==='completed'),seats=complete.reduce((n,t)=>n+t.capacity,0),boarded=complete.reduce((n,t)=>n+t.boarded,0);
  $('#content').innerHTML=`<div class="hint">Gerçekleşen doluluk yalnızca tamamlanan seferlerde binen yolcu / koltuk toplamıdır. Aynı kişi birden fazla sefere bindiyse her biniş sayılır. Yoklama operatör beyanıdır; GPS veya kart okuyucu doğrulaması içermez.</div><div class="section-toolbar"><h2>Gerçekleşen operasyon</h2><a class="secondary" href="/api/report.csv" download="servisplan-sefer-raporu.csv">↓ Sefer raporu (CSV)</a></div><div class="record-grid"><div class="record"><p>Tamamlanan sefer</p><div class="report-number">${complete.length}</div></div><div class="record"><p>Toplam biniş</p><div class="report-number">${boarded}</div></div><div class="record"><p>Gerçekleşen doluluk</p><div class="report-number">%${fmt(seats?boarded/seats*100:0)}</div></div></div><br><div class="panel"><div class="panel-heading"><h2>Hat bazında performans</h2><small>Tüm tamamlanan seferler</small></div><div class="table-wrap"><table><thead><tr><th>HAT</th><th>SEFER</th><th>PLANLANAN YOLCU</th><th>BİNİŞ</th><th>GELMEDİ</th><th>DOLULUK</th></tr></thead><tbody>${state.routes.map(r=>{const trips=complete.filter(t=>t.route_id===r.id),booked=trips.reduce((n,t)=>n+t.booked,0),actual=trips.reduce((n,t)=>n+t.boarded,0),capacity=trips.reduce((n,t)=>n+t.capacity,0);return `<tr><td>${esc(r.name)}</td><td>${trips.length}</td><td>${booked}</td><td>${actual}</td><td>${booked-actual}</td><td>%${fmt(capacity?actual/capacity*100:0)}</td></tr>`;}).join('')}</tbody></table></div></div><br><div class="panel"><div class="panel-heading"><h2>İşlem günlüğü</h2><small>Son 25 hareket</small></div><div class="table-wrap"><table><thead><tr><th>İŞLEM</th><th>DETAY</th><th>ZAMAN</th></tr></thead><tbody>${state.events.map(e=>`<tr><td>${esc(e.action)}</td><td>${esc(e.detail)}</td><td>${esc(e.created_at.replace('T',' '))}</td></tr>`).join('')}</tbody></table></div></div>`;
}
function field(key,title,type='text',options=[],value='',wide=false){
  let control;
  if(type==='select')control=`<select name="${key}" required>${options.map(o=>`<option value="${esc(o.value)}" ${String(value)===String(o.value)?'selected':''}>${esc(o.label)}</option>`).join('')}</select>`;
  else control=`<input name="${key}" type="${type}" value="${esc(value)}" ${type==='number'?`min="${key==='minute'?0:1}" max="${{capacity:100,duration:720,minute:719}[key]||1000000}" step="1"`:'maxlength="100"'} required>`;
  return `<label class="${wide?'wide':''}">${title}${control}</label>`;
}
function options(items,key='name'){return items.map(x=>({value:x.id,label:x[key]}));}
function openForm(type,extra={}){
  const form=$('#form');form.reset();form.dataset.type=type;delete form.dataset.passenger;delete form.dataset.route;
  let html='',title='',note='';
  if(type==='vehicles'){title='Araç ekle';html=field('code','Araç kodu / plaka')+field('model','Araç tanımı')+field('capacity','Yolcu koltuğu kapasitesi','number')+field('status','Araç durumu','select',[{value:'ready',label:'Sefere hazır'},{value:'maintenance',label:'Bakımda'}]);}
  if(type==='drivers'){title='Sürücü ekle';html=field('name','Sürücü adı / demo etiketi')+field('code','Sürücü kodu');}
  if(type==='routes'){title='Hat oluştur';html=field('name','Hat adı')+field('destination','Varış noktası')+field('duration','Toplam yolculuk süresi (dk)','number');note='Hattı kaydettikten sonra duraklarını ekleyebilirsin.';}
  if(type==='stops'){title='Hatta durak ekle';form.dataset.route=extra.route_id;html=field('name','Durak adı')+field('minute','Kalkıştan itibaren dakika','number',[],0);note='Aynı hatta iki durağın dakikası aynı olamaz. Dakika, hat süresinden küçük olmalı.';}
  if(type==='passengers'){title='Yolcu ekle';html=field('name','Yolcu adı / demo etiketi')+field('organization','Kurum / grup');note='Kaydettikten sonra yolcuyu bir durağa atayabilirsin.';}
  if(type==='assign'){
    if(!state.stops.length){notify('Önce bir hat ve durak ekleyin.');return;}
    title='Yolcuyu durağa ata';form.dataset.passenger=extra.passenger_id;
    const passenger=state.passengers.find(p=>p.id===extra.passenger_id);
    html=field('stop_id','Hat / durak','select',state.stops.map(s=>({value:s.id,label:`${state.routes.find(r=>r.id===s.route_id).name} · ${s.name} (+${s.minute} dk)`})),passenger.stop_id||'',true);note='Mevcut sefer listeleri değişmez. Atama yeni planlanan seferlere yansır.';
  }
  if(type==='trips'){
    if(!state.routes.length||!state.vehicles.some(v=>v.status==='ready')||!state.drivers.length){notify('Sefer için hat, hazır araç ve sürücü ekleyin.');return;}
    title='Sefer planla';html=field('route_id','Hat','select',options(state.routes),extra.route_id||'')+field('vehicle_id','Araç','select',state.vehicles.filter(v=>v.status==='ready').map(v=>({value:v.id,label:`${v.code} · ${v.capacity} koltuk`})))+field('driver_id','Sürücü','select',options(state.drivers))+field('date','Kalkış tarihi','date',[],today)+field('time','Kalkış saati','time',[],'07:30');note='Kapasite ve zaman çakışmaları kontrol edilir. Saatler Europe/Istanbul; yolcu listesi kayıt anındaki durak atamalarından alınır.';
  }
  $('#dialog-title').textContent=title;$('#fields').innerHTML=html;$('#form-note').textContent=note;$('#form-error').textContent='';$('#dialog').showModal();
}
function renderManifest(){
  const trip=state.trips.find(t=>t.id===manifestId);if(!trip)return;
  $('#manifest-title').textContent=`#${trip.id} · ${trip.route_name}`;
  const members=state.manifest.filter(m=>m.trip_id===manifestId),pending=members.filter(m=>m.attendance==='pending').length;
  $('#manifest-content').innerHTML=`<div class="hint">${esc(trip.vehicle_code)} · ${esc(trip.driver_name)}<br>${esc(trip.departure.replace('T',' '))} · ${trip.booked} planlanan / ${trip.capacity} koltuk<br>${trip.boarded} bindi · ${pending} yoklama bekliyor</div><p class="muted">${trip.status==='running'?'Her yolcu için “Bindi” veya “Gelmedi” seçin. Sefer tamamlanana kadar düzeltilebilir.':'Yoklama için önce seferi başlatın. Tamamlanan ve iptal edilen kayıtlar değiştirilemez.'}</p>${members.map(m=>`<div class="manifest-row"><div><b>${esc(m.name)}</b><small>${esc(m.stop_name)} · +${m.minute} dk</small></div>${trip.status==='running'?`<div class="attendance-buttons">${[['boarded','Bindi'],['absent','Gelmedi'],['pending','Sıfırla']].map(([key,title])=>`<button class="${key==='absent'?'absent ':''}${m.attendance===key?'chosen':''}" data-attendance="${m.passenger_id}:${key}" aria-pressed="${m.attendance===key}">${title}</button>`).join('')}</div>`:badge(m.attendance)}</div>`).join('')||empty('Bu seferin yolcu listesi boş. Yeni yolcu atamaları mevcut sefere eklenmez.')}`;
}
$('#nav').addEventListener('click',e=>{const b=e.target.closest('[data-view]');if(b&&state)go(b.dataset.view);});
$('#main-action').addEventListener('click',()=>{if(state)openForm({routes:'routes',fleet:'vehicles',passengers:'passengers'}[view]||'trips');});
$('#close-dialog').addEventListener('click',()=>$('#dialog').close());
$('#close-manifest').addEventListener('click',()=>$('#manifest-dialog').close());
$('#content').addEventListener('click',async e=>{
  const b=e.target.closest('button');if(!b)return;
  if(b.dataset.go)go(b.dataset.go);
  if(b.dataset.open)openForm(b.dataset.open);
  if(b.dataset.filter){tripFilter=b.dataset.filter;render();}
  if(b.hasAttribute('data-all-dates')){tripDate='';renderTrips();}
  if(b.dataset.stop)openForm('stops',{route_id:Number(b.dataset.stop)});
  if(b.dataset.routeTrip)openForm('trips',{route_id:Number(b.dataset.routeTrip)});
  if(b.dataset.assign)openForm('assign',{passenger_id:Number(b.dataset.assign)});
  if(b.dataset.manifest){manifestId=Number(b.dataset.manifest);renderManifest();$('#manifest-dialog').showModal();}
  if(b.dataset.unassign||b.dataset.transition){
    b.disabled=true;
    try{
      if(b.dataset.unassign)await api('/api/unassign',{passenger_id:Number(b.dataset.unassign)});
      else{const [id,status]=b.dataset.transition.split(':');await api('/api/trips/status',{trip_id:Number(id),status});}
      await refresh();notify('Operasyon kaydı güncellendi.');
    }catch(error){notify(error.message);b.disabled=false;}
  }
});
$('#manifest-content').addEventListener('click',async e=>{
  const b=e.target.closest('[data-attendance]');if(!b)return;b.disabled=true;
  try{const [passenger,attendance]=b.dataset.attendance.split(':');await api('/api/attendance',{trip_id:manifestId,passenger_id:Number(passenger),attendance});await refresh();renderManifest();}catch(error){notify(error.message);b.disabled=false;}
});
$('#form').addEventListener('submit',async e=>{
  e.preventDefault();const b=e.target.querySelector('[type=submit]');b.disabled=true;
  const data=Object.fromEntries(new FormData(e.target));if(e.target.dataset.passenger)data.passenger_id=Number(e.target.dataset.passenger);if(e.target.dataset.route)data.route_id=Number(e.target.dataset.route);
  try{await api(`/api/${e.target.dataset.type}`,data);$('#dialog').close();await refresh();notify('Kayıt oluşturuldu. Operasyon planı güncellendi.');}catch(error){$('#form-error').textContent=error.message;}finally{b.disabled=false;}
});
refresh().catch(error=>{$('#content').textContent=`Veriler yüklenemedi: ${error.message}. Sunucuyu kontrol edip sayfayı yenileyin.`;});
