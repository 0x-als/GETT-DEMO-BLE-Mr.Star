import asyncio
import logging
import threading
from dataclasses import dataclass

from bleak import BleakClient, BleakScanner

from config import Config
from service.color import LedProtocol


class AsyncRunner:
    def __init__(self):
        self.loop = asyncio.new_event_loop()
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self):
        asyncio.set_event_loop(self.loop)
        self.loop.run_forever()

    def submit(self, coro):
        return asyncio.run_coroutine_threadsafe(coro, self.loop)


@dataclass
class BleDevice:
    address: str
    name: str
    rssi: int
    led: bool


class ReconnectPolicy:
    def __init__(self, delays):
        self.delays = delays
        self.attempt = 0

    def next_delay(self):
        delay = self.delays[min(self.attempt, len(self.delays) - 1)]
        self.attempt += 1
        return delay

    def reset(self):
        self.attempt = 0


class BleConnection:
    def __init__(self, runner, events):
        self.runner = runner
        self.events = events
        self.client = None
        self._write_lock = None
        self._connect_lock = None

    def connect_to(self, address):
        self.runner.submit(self._connect(address))

    def send(self, *packets):
        self.runner.submit(self._write(*packets))

    def power_off_blocking(self, timeout=4):
        self.runner.submit(self._power_off()).result(timeout=timeout)

    def disconnect_now(self, timeout=3):
        try:
            self.runner.submit(self._disconnect()).result(timeout=timeout)
        except Exception:
            pass

    def _on_lost(self, client):
        if client is self.client:
            self.events.emit("disconnected")

    def _get_write_lock(self):
        if self._write_lock is None:
            self._write_lock = asyncio.Lock()
        return self._write_lock

    def _get_connect_lock(self):
        if self._connect_lock is None:
            self._connect_lock = asyncio.Lock()
        return self._connect_lock

    async def _connect(self, address):
        async with self._get_connect_lock():
            if (
                self.client
                and self.client.is_connected
                and str(self.client.address).upper() == address.upper()
            ):
                self.events.emit("connected")
                return
            self.events.emit("connecting")
            previous, self.client = self.client, None
            if previous:
                try:
                    await previous.disconnect()
                except Exception:
                    pass
            try:
                client = BleakClient(
                    address,
                    timeout=Config.CONNECT_TIMEOUT,
                    disconnected_callback=self._on_lost,
                )
                self.client = client
                await client.connect()
                self.events.emit("connected")
            except Exception as exc:
                self.client = None
                self.events.emit("connect_error", f"{type(exc).__name__}: {exc}")

    async def _disconnect(self):
        client, self.client = self.client, None
        try:
            if client and client.is_connected:
                await client.disconnect()
        except Exception:
            pass

    async def _write(self, *packets):
        if not self.client or not self.client.is_connected:
            return
        async with self._get_write_lock():
            try:
                for packet in packets:
                    await self.client.write_gatt_char(
                        Config.WRITE_UUID, packet, response=False
                    )
                    self.events.emit("sent", packet.hex(" ").upper())
                    await asyncio.sleep(0.025)
            except Exception as exc:
                self.events.emit("write_error", f"{type(exc).__name__}: {exc}")

    async def _power_off(self):
        if not self.client or not self.client.is_connected:
            logging.info("Лента не подключена — выключать нечего")
            return
        async with self._get_write_lock():
            for _ in range(2):
                await self.client.write_gatt_char(
                    Config.WRITE_UUID, LedProtocol.POWER_OFF, response=False
                )
                await asyncio.sleep(0.15)
        logging.info("Команда выключения отправлена")


class DeviceScanner:
    def __init__(self, runner, events):
        self.runner = runner
        self.events = events

    def start(self, timeout=None):
        self.runner.submit(self._scan(timeout or Config.SCAN_TIMEOUT))

    async def _scan(self, timeout):
        try:
            found = await BleakScanner.discover(timeout=timeout, return_adv=True)
            devices = []
            for device, adv in found.values():
                uuids = [u.lower() for u in (adv.service_uuids or [])]
                devices.append(
                    BleDevice(
                        address=device.address,
                        name=device.name or adv.local_name or "",
                        rssi=adv.rssi,
                        led=any("fff0" in u for u in uuids),
                    )
                )
            self.events.emit("scan_done", devices)
        except Exception as exc:
            self.events.emit("scan_error", f"{type(exc).__name__}: {exc}")
