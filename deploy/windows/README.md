# Агентни Windows ноутбукда ишга тушириш

Агент ноутбукингизда яшайди: база, мижоз ҳужжатлари ва каталог шу компьютерда сақланади. Интернет мижозлар билан алоқа ва AI учун ишлатилади.

## 1. Юклаб олиш

GitHub'да репозиторийни очинг ва `ccr-d5c74dc0-175va0` бранчини танланг. **Code → Download ZIP** ни босинг ва архивни бўш жойсиз оддий папкага очинг, масалан `C:\NamTour`.

## 2. Ўрнатиш (10–15 дақиқа)

`deploy\windows\install.bat` ни икки марта босинг. Скрипт:

1. Python 3.12'ни текширади, керак бўлса ўзи ўрнатади;
2. кутубхоналарни ўрнатади;
3. `.env` файлини очади: қуйидаги жадвалдагиларни тўлдиринг, сақланг ва Блокнотни ёпинг;
4. базани тайёрлайди;
5. Windows'га кирганингизда агент ўзи ишга тушадиган қилади;
6. ҳар куни соат 03:00 да захира нусха оладиган қилади;
7. сўраб, зарядкага уланганда ноутбук ухламайдиган қилади;
8. сўраб, Cloudflare Tunnel'ни ўрнатади (Instagram, WhatsApp ва телефон учун);
9. агентни ишга тушириб, ишлаётганини текширади.

`.env` да биринчи навбатда тўлдириладиганлар:

| Калит | Нима | Қаердан |
|---|---|---|
| `SALES_BOT_TOKEN` | мижозлар учун Telegram бот | @BotFather → /newbot |
| `BOT_TOKEN` | менежерлар учун бот (`/leads`, `/reply`, `/paid`) | @BotFather, иккинчи бот |
| `DIRECTOR_IDS`, `SALES_MANAGER_IDS` | ким аризаларни олади | @userinfobot |
| `SALES_SHOP_NAME` | `Jihan Jan Jasur Nam Tour` | — |
| `SALES_PAYMENT_CARD`, `SALES_USD_RATE` | олдиндан тўлов учун карта ва доллар курси | — |
| `OPENAI_API_KEY` | ихтиёрий: ақллироқ жавоб ва овозли хабарлар | platform.openai.com |
| `API_TOKEN` | панел пароли (`http://127.0.0.1:8000/panel`) | ўрнатувчи ўзи яратади ва экранда кўрсатади |

**Шу билан Telegram ишлайди.** Мижозлар учун ботга ёзиб текшириб кўринг.

## 3. Кунлик бошқарув

| Файл | Нима қилади |
|---|---|
| `status.bat` | агент ишлаяптими ва журналнинг охирги қаторлари |
| `stop.bat` | агентни тўхтатиш |
| `visual.bat` | 3D схемалар ва буйруқлар рўйхатини браузерда очиш |
| `install.bat` | қайта ўрнатиш ёки янгилаш (`.env` ўзгармайди) |
| `uninstall.bat` | автоматик ишга тушиш ва захирани ўчириш (маълумотлар қолади) |

Журналлар `data\logs` папкасида туради. `.env` ўзгарса, `stop.bat` ни босинг ва Windows'га қайта киринг, ёки PowerShell'да `Start-ScheduledTask NamTourAgent` ни ишга туширинг.

## 4. Захира нусха Google Drive'га

[Google Drive for desktop](https://www.google.com/drive/download/) ни ўрнатинг. Ўрнатишда захира папкаси сўралганда Drive папкасини кўрсатинг, масалан `G:\My Drive\NamTour`.

Ҳар куни `agent_backup_САНА.zip` файли яратилади: унда база ва мижоз ҳужжатлари бўлади. Охирги 14 таси сақланади. Қўлда захира олиш учун: `.venv\Scripts\python.exe -m app.main backup --to "G:\My Drive\NamTour"`.

Захирани тиклаш: агентни тўхтатинг, zip ичидаги `database.db` ни `data\nashriyot.db` ўрнига, `lead_files` папкасини эса `data\lead_files` ўрнига қўйинг.

## 5. Cloudflare Tunnel (Instagram, WhatsApp, телефон учун)

Бу хизматлар ноутбукка интернетдан HTTPS орқали етиб келиши керак. Тоннель бепул, лекин сизга домен керак (йилига тахминан 10 $). Доменни Cloudflare'га улаб қўйинг.

PowerShell'да қуйидагиларни бажаринг:

```powershell
cloudflared tunnel login                      # браузер очилади, доменни танланг
cloudflared tunnel create namtour             # TUNNEL-ID ва .json файл яратилади
cloudflared tunnel route dns namtour agent.SIZNING-DOMEN.uz
```

`deploy\windows\cloudflared-config.yml` ни `%USERPROFILE%\.cloudflared\config.yml` га нусха қилинг. Ичидаги `<TUNNEL-ID>`, фойдаланувчи номи ва доменни алмаштиринг. Кейин тоннелни Windows хизмати қилиб ўрнатинг (PowerShell'ни администратор сифатида очинг):

```powershell
cloudflared service install
```

Шундан кейин webhook манзиллари:
- Meta (Instagram, WhatsApp, Messenger): `https://agent.SIZNING-DOMEN.uz/webhooks/meta`
- Twilio (телефон): `https://agent.SIZNING-DOMEN.uz/webhooks/twilio/voice`

`.env` га `SALES_PUBLIC_BASE_URL=https://agent.SIZNING-DOMEN.uz` ни ҳам ёзинг.

Интернетга фақат `/webhooks/` йўли очилади. `/api` ва бошқарув фақат ноутбукнинг ўзида қолади.

## Муҳим

- **Ноутбук ёқиқ ва интернетга уланган бўлиши керак.** Ўчса, агент жим қолади. Ёқилганда мижозлар ёзган хабарлар келади: Telegram уларни 24 соатгача сақлайди, Meta эса бир неча соат қайта юборади.
- **Батареяда ноутбук одатдагидек ухлайди**, фақат зарядкада ухламайди.
- **Скриптлар ҳақиқий Windows'да ҳали синалмаган** (улар Linux'да ёзилган). Хато чиқса, экрандаги матнни юборинг.
