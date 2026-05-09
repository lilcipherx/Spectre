# Spectre — установка с нуля (альбомная 320×240)

Полное руководство для Raspberry Pi 4 + Pi OS Trixie + ILI9341 SPI TFT 320×240
+ USB-микрофон AB13X.

Эта версия использует **kernel framebuffer (`/dev/fb1`)** вместо прямой работы со SPI.
Так надёжнее: ядро само рулит панелью, подсветкой и SPI-шиной, а Spectre просто
рисует кадр в `/dev/fb1`.

---

## ШАГ 0. Что должно быть

- Raspberry Pi 4 (или 3B+), карта с Pi OS Trixie
- Подключён ILI9341 SPI TFT по проводам (DC=GPIO24, RST=GPIO25, CS=CE0,
  подсветка LED=GPIO18)
- Подключён USB-микрофон / USB-аудиокарта (AB13X)
- Интернет
- API-ключ ElevenLabs (`sk_…`) — https://elevenlabs.io/app/settings/api-keys

---

## ШАГ 1. Снести всё старое (если ставил раньше)

Скопируй и вставь в терминал малины целиком:

```bash
sudo systemctl stop spectre 2>/dev/null
sudo systemctl disable spectre 2>/dev/null
sudo rm -f /etc/systemd/system/spectre.service
sudo systemctl daemon-reload
sudo systemctl reset-failed spectre 2>/dev/null

sudo rm -rf /opt/spectre
rm -rf ~/Spectre ~/Desktop/Spectre ~/spectre
rm -f ~/Spectre.tar.gz ~/Desktop/Spectre.tar.gz

# Восстановить config.txt из бэкапа (если был старый kiosk)
if [ -f /boot/firmware/config.txt.spectre.bak ]; then
  sudo cp /boot/firmware/config.txt.spectre.bak /boot/firmware/config.txt
  sudo rm -f /boot/firmware/config.txt.spectre.bak
fi

echo "=== очистка завершена ==="
```

---

## ШАГ 2. Убедиться что fbtft-overlay в /boot/firmware/config.txt — правильный

Открой:
```bash
sudo nano /boot/firmware/config.txt
```

Найди строку с `dtoverlay=fbtft...` или `dtoverlay=tft...` или `dtoverlay=ili9341...`.
Должно быть примерно так (одной строкой):

```
dtoverlay=fbtft,spi0-0,ili9341,bgr,reset_pin=25,dc_pin=24,led_pin=18,rotate=90,speed=32000000
```

Если такой строки нет — добавь её внизу файла. Если есть — убедись что:
- `reset_pin=25` (RST → GPIO25)
- `dc_pin=24` (DC → GPIO24)
- `led_pin=18` (подсветка → GPIO18)
- `rotate=90` (альбомная 320×240) — это самое важное!

Также проверь что выше в файле есть `dtparam=spi=on`.

Сохрани (Ctrl+O, Enter, Ctrl+X) и перезагрузись:
```bash
sudo reboot
```

После загрузки убедись что `/dev/fb1` появился:
```bash
ls -la /dev/fb1
cat /sys/class/graphics/fb1/virtual_size
```
- `/dev/fb1` должен быть с правами `crw-rw---- root video`
- `virtual_size` должен показать `320,240` (если будет `240,320` — у тебя
  портретная ориентация; в шаге 4 мы это поправим)

---

## ШАГ 3. Скачать архив и установить

```bash
cd ~
wget -O Spectre.tar.gz "<СЮДА_ССЫЛКУ_ИЗ_СЛЕДУЮЩЕГО_СООБЩЕНИЯ>"
ls -lh Spectre.tar.gz   # ~230K
tar -xzf Spectre.tar.gz
cd ~/Spectre
./scripts/install.sh --kiosk
```

`--kiosk` отключает рабочий стол на HDMI (boot-to-console), так что Pi
загружается в текстовый режим, и Spectre сразу владеет TFT. SSH остаётся
включённым.

Скрипт:
- Поставит системные пакеты (python3, alsa-utils, шрифты, GPIO/SPI Python-биндинги)
- Добавит твоего юзера в группы `audio video spi gpio` (если ещё нет)
- Создаст venv в `/opt/spectre/.venv`
- Установит Python-зависимости (без libportaudio — он не нужен)
- Поставит systemd-юнит `/etc/systemd/system/spectre.service`

Жди до строки `>> done`. 2-5 минут.

---

## ШАГ 4. Прописать API-ключ и микрофон

```bash
sudo nano /opt/spectre/.env
```

Внутри:
- `ELEVENLABS_API_KEY=` → впиши свой `sk_…`
- `SPECTRE_AUDIO_DEVICE=plughw:CARD=Audio,DEV=0` — это значение по умолчанию,
  должно работать для любой USB-карты с именем `Audio`. Не меняй если в
  `arecord -l` твоя карта называется `card N: Audio [...]`.
- `SPECTRE_DISPLAY=fb1` — оставь так (это новый бэкенд через `/dev/fb1`)

Сохрани (Ctrl+O, Enter, Ctrl+X).

---

## ШАГ 5. Запустить

```bash
sudo systemctl restart spectre
sleep 5
systemctl status spectre --no-pager | head -10
sudo journalctl -u spectre -n 30 --no-pager
```

**Что должно быть:**
- Status: `Active: active (running)` без `failed` / `auto-restart`
- В логах:
  - `display: /dev/fb1 320x240 16bpp (kernel framebuffer)`
  - `audio: starting arecord -q -D plughw:CARD=Audio,DEV=0 ...`
  - `audio: capture started ...`
  - `stt: connecting (lang=uz, model=scribe_v2_realtime)`
- На TFT — **SPECTRE HUD** (альбомно): чёрный фон, тёмно-зелёный статус-бар
  сверху, слева `SPECTRE`, посередине `○ RX`, справа часы `HH:MM:SS`,
  снизу `LANG UZ` и `IDLE`. Без миганий, картинка стабильная.

**Если API-ключ не вписан** или вписан неправильно — на TFT появится сообщение
«NO API KEY — configure .env», и сервис останется в нём (без crash-loop).

---

## ШАГ 6. Тест: говоришь в микрофон → текст на TFT

Прижми PTT на радио (или просто говори в USB-микрофон AB13X). На TFT через
200–500 мс должен появиться твой текст:
- **жёлтым** мерцающим — partial (живая транскрипция в процессе)
- **белым** с amber-меткой `HH:MM` — final (зафиксированный сегмент)

Хвостовать логи в реальном времени:
```bash
sudo journalctl -u spectre -f
```

---

## Что если что-то не работает

| Симптом | Что проверить |
|---|---|
| TFT мигает белым/чёрным | Сервис крашится в цикле. `sudo journalctl -u spectre -n 50` — смотри ERROR в конце |
| `cannot read /sys/class/graphics/fb1/virtual_size` | fbtft-overlay не загружен. Проверь `dtoverlay=fbtft...` в `/boot/firmware/config.txt`, перезагрузись |
| `cannot open /dev/fb1: Permission denied` | Юзер не в группе `video`. Запусти `sudo usermod -aG video $(whoami)`, перезагрузись |
| `arecord not found` | `sudo apt install alsa-utils` |
| `arecord exited non-zero rc=1` | Неправильное имя устройства в `SPECTRE_AUDIO_DEVICE`. Проверь `arecord -l` и впиши `plughw:N,0` или `plughw:CARD=Audio,DEV=0` |
| На TFT «NO API KEY» | `.env` без ключа. Впиши `ELEVENLABS_API_KEY=sk_…` и `sudo systemctl restart spectre` |
| TFT в портретной ориентации (240×320 вместо 320×240) | В `/boot/firmware/config.txt` строка `dtoverlay=fbtft,...,rotate=90,...` — поменяй `rotate=90` на `rotate=270` (или наоборот), перезагрузись |
| TFT совсем чёрный, не мигает, нет реакции | Проводка / питание подсветки (LED-пин на GPIO18). Проверь VCC/GND/LED на TFT |
| `Active: failed` после перезагрузки | Проверь journalctl, скорее всего сервис стартанул раньше чем появился `/dev/fb1`. `sudo systemctl restart spectre` решает |

---

## Дополнительно: вернуть рабочий стол на HDMI

`--kiosk` отключает дисплей-менеджер. Если хочешь вернуть рабочий стол на
HDMI (Spectre на TFT останется работать как был):

```bash
sudo apt install -y lightdm
sudo systemctl set-default graphical.target
sudo reboot
```

---

## SSH с ноутбука (опционально)

После установки можешь подключаться:
```
ssh lilcipher@192.168.X.X
```

IP видно при загрузке Pi на HDMI или командой `hostname -I` на самой малине.
