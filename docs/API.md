# ServisPlan HTTP API

Taban adresi: `http://127.0.0.1:8001`. Yazma istekleri JSON nesnesi ve `Content-Type: application/json` kullanır. Başarılı yazma `201`, doğrulama `400`, kaynak kontrolü `403`, içerik türü `415`, bulunamayan yol `404`, veritabanı hatası `503` döner. Hatalar `{"error":"Açıklama"}` biçimindedir.

| Metot | Uç nokta | Açıklama |
|---|---|---|
| GET | `/api/state` | Tüm ekranlar için tutarlı veri görüntüsü |
| GET | `/api/report.csv` | Sefer bazlı UTF-8 BOM CSV |
| POST | `/api/vehicles` | Araç ekle |
| POST | `/api/drivers` | Sürücü ekle |
| POST | `/api/routes` | Hat ekle |
| POST | `/api/stops` | Hatta durak ekle |
| POST | `/api/passengers` | Yolcu ekle |
| POST | `/api/assign` | Yolcunun durak atamasını oluştur/değiştir |
| POST | `/api/unassign` | Durak atamasını kaldır |
| POST | `/api/trips` | Kapasite/çakışma kontrolüyle sefer oluştur |
| POST | `/api/trips/status` | Sefer durumu değiştir |
| POST | `/api/attendance` | Devam eden seferde yoklama kaydet |

## Temel kayıtlar

```json
{"code":"DEMO-05","model":"Demo minibüs","capacity":16,"status":"ready"}
```

Araç kapasitesi 1–100; durum `ready` veya `maintenance`. Araç kodu benzersizdir, büyük harfe dönüştürülür.

```json
{"name":"Örnek Sürücü","code":"SRC-004"}
```

Sürücü kodu benzersizdir.

```json
{"name":"Doğu Hattı","destination":"Demo Kampüs","duration":50}
```

Hat süresi 1–720 dakika.

```json
{"route_id":1,"name":"Örnek Durak","minute":18}
```

Durak dakikası 0–719, hat süresinden küçük ve hat içinde benzersiz olmalı.

```json
{"name":"Örnek Yolcu 33","organization":"Demo Kurum"}
```

Yeni kayıt yanıtı: `{"id":33}`. Metinler boş olamaz; genel üst sınır 100, araç/sürücü kodunda 20 karakterdir.

## Yolcu atamaları

```json
{"passenger_id":33,"stop_id":1}
```

`/api/assign` mevcut atamayı değiştirir. Tek kişiye birden fazla durak ataması tutulmaz. `/api/unassign` için yalnızca `passenger_id` gönderin. Var olan sefer manifestoları değişmez.

## Sefer planlama

```json
{"route_id":1,"vehicle_id":1,"driver_id":1,"date":"2030-01-10","time":"09:00"}
```

Saatler Europe/Istanbul olarak yorumlanır. Varış, kalkış + hat süresidir. Atanmış yolcu sayısı araç kapasitesini aşarsa veya planlanan/devam eden seferler arasında araç, sürücü veya yolcu çakışması varsa `400` döner. Hattın en az bir durağı bulunmalıdır. Boş yolcu listeli sefer planlanabilir.

## Durum değişikliği

```json
{"trip_id":5,"status":"running"}
```

Geçişler: `planned → running`, `planned → cancelled`, `running → completed`. Tamamlamada tüm yolcuların yoklaması `boarded` veya `absent` olmalı. Çakışan kaynağın devam eden seferi varsa başlatma reddedilir. İptal, kaynakların planlanan zaman aralığını serbest bırakır.

## Yoklama

```json
{"trip_id":5,"passenger_id":1,"attendance":"boarded"}
```

Değerler: `boarded`, `absent`, `pending`. Yalnızca `running` seferin kendi listesinde bulunan yolcu güncellenebilir. `pending` ile karar sıfırlanabilir; sonrasında sefer tamamlama tekrar engellenir.

## State yanıtı

`vehicles`, `drivers`, `routes`, `stops`, `passengers`, `trips`, `manifest`, `events` dizilerini içerir. `routes.passengers` güncel durak atamalarının sayısıdır; `trips.booked` planlama anındaki sabit manifestodur. `trips.boarded` bindi işaretli kişileri sayar. `events` son 25 hareketle sınırlıdır. CSV yolcu isimlerini içermez; sefer toplamlarını içerir.
