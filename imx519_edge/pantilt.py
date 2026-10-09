"""パンチルト（Arducam B0283）。PCA9685 を I2C（i2c-1、0x40）で動かす。

- 動かしたあとは full-off（LEDn_OFF_H の bit 4）で PWM を止める。
  出し続けると、サーボが位置を保とうとして微振動する
- 構図を再現したいときは、目標には常に同じ方向から近づける（バックラッシュの影響をそろえる）
- 超解像用のズレは、ランダムに動かしてから同じ指令値に戻し、その戻り誤差で作る（DESIGN.md）
"""

import json
import random
import time
from pathlib import Path

from .config import PanTiltConfig

MODE1, PRESCALE, LED0_ON_L, ALL_LED_OFF_H = 0x00, 0xFE, 0x06, 0xFD
SLEEP, AUTO_INCREMENT, RESTART = 0x10, 0x20, 0x80
FULL_OFF = 0x10
OSC_HZ = 25_000_000  # 内部発振器（typical。実周波数は要較正）


class PCA9685:
    def __init__(self, bus, address: int = 0x40, freq_hz: float = 50.0):
        self.bus, self.address = bus, address
        self.set_freq(freq_hz)

    @classmethod
    def open(cls, bus_number: int, address: int, freq_hz: float) -> "PCA9685":
        from smbus2 import SMBus

        return cls(SMBus(bus_number), address, freq_hz)

    def set_freq(self, freq_hz: float) -> None:
        prescale = round(OSC_HZ / (4096 * freq_hz)) - 1
        old = self.bus.read_byte_data(self.address, MODE1)
        self.bus.write_byte_data(
            self.address, MODE1, (old & 0x7F) | SLEEP
        )  # プリスケールは SLEEP 中だけ書ける
        self.bus.write_byte_data(self.address, PRESCALE, prescale)
        self.bus.write_byte_data(self.address, MODE1, (old & ~SLEEP) | AUTO_INCREMENT)
        time.sleep(0.005)
        self.bus.write_byte_data(self.address, MODE1, (old & ~SLEEP) | AUTO_INCREMENT | RESTART)

    def set_ticks(self, channel: int, ticks: int) -> None:
        reg = LED0_ON_L + 4 * channel
        self.bus.write_i2c_block_data(self.address, reg, [0, 0, ticks & 0xFF, (ticks >> 8) & 0x0F])

    def full_off(self, channel: int) -> None:
        self.bus.write_byte_data(self.address, LED0_ON_L + 4 * channel + 3, FULL_OFF)

    def all_off(self) -> None:
        self.bus.write_byte_data(self.address, ALL_LED_OFF_H, FULL_OFF)


class PanTilt:
    def __init__(self, driver: PCA9685, config: PanTiltConfig, sleep=time.sleep):
        self.driver, self.config, self.sleep = driver, config, sleep
        self.pan: int | None = None  # 最後に送った指令値（起動直後は分からない）
        self.tilt: int | None = None

    def clamp(self, pan: int, tilt: int) -> tuple[int, int]:
        c = self.config
        return min(max(pan, c.pan_min), c.pan_max), min(max(tilt, c.tilt_min), c.tilt_max)

    def _send(self, pan: int, tilt: int) -> None:
        self.driver.set_ticks(self.config.pan_channel, pan)
        self.driver.set_ticks(self.config.tilt_channel, tilt)
        self.pan, self.tilt = pan, tilt
        self.sleep(self.config.move_wait_s)

    def release(self) -> None:
        """PWM を止める（指令値は覚えておく）。"""
        self.driver.full_off(self.config.pan_channel)
        self.driver.full_off(self.config.tilt_channel)

    def move(self, pan: int, tilt: int, approach: str | None = None, settle: bool = True) -> dict:
        """目標へ動かして止める。approach が "+" / "-" なら、その向きから近づける。"""
        pan, tilt = self.clamp(pan, tilt)
        if approach in ("+", "-"):
            d = self.config.approach_ticks * (-1 if approach == "+" else 1)
            self._send(*self.clamp(pan + d, tilt + d))
        self._send(pan, tilt)
        self.release()
        if settle:
            self.sleep(self.config.settle_s)
        return {"pan": pan, "tilt": tilt, "approach": approach}

    def jitter(self, pan: int, tilt: int, ticks: tuple[int, int], rng: random.Random) -> dict:
        """ランダムな量・向きへ一度動かしてから、同じ指令値へ戻す（戻り誤差をズレに使う）。"""
        lo, hi = ticks
        dp = rng.randint(lo, hi) * rng.choice((-1, 1))
        dt = rng.randint(lo, hi) * rng.choice((-1, 1))
        self._send(*self.clamp(pan + dp, tilt + dt))
        result = self.move(pan, tilt)
        # 戻る向きは、ずらした側の反対（パンの向きで代表させる）
        return {**result, "approach": "-" if dp > 0 else "+", "offset": [dp, dt]}

    def stop(self) -> None:
        """非常停止（全チャンネル off）。"""
        self.driver.all_off()


class Presets:
    """構図のプリセット（名前 → 指令値）。JSON ファイルに置く。"""

    def __init__(self, path: Path):
        self.path = path

    def load(self) -> dict[str, dict]:
        try:
            return json.loads(self.path.read_text())
        except FileNotFoundError:
            return {}

    def save(self, presets: dict[str, dict]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(presets, indent=1, ensure_ascii=False))


class FakeBus:
    """smbus2 の代わり（書き込みを記録するだけ）。"""

    def __init__(self):
        self.writes: list[tuple] = []
        self.regs: dict[int, int] = {MODE1: 0x11}

    def read_byte_data(self, address: int, reg: int) -> int:
        return self.regs.get(reg, 0)

    def write_byte_data(self, address: int, reg: int, value: int) -> None:
        self.writes.append(("byte", reg, value))
        self.regs[reg] = value

    def write_i2c_block_data(self, address: int, reg: int, data: list[int]) -> None:
        self.writes.append(("block", reg, list(data)))
