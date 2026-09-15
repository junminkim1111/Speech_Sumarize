#!/usr/bin/env python3
"""BlackHole 로 맥의 시스템 소리를 녹음한다.

맥의 출력 장치가 BlackHole(또는 BlackHole 을 포함한 다중 출력)로 되어 있어야
소리가 잡힌다. 스피커로 두면 파일은 만들어지지만 전부 무음이다.
그래서 녹음이 끝나면 볼륨을 재서 무음이면 알려준다.

컨테이너로 Ogg/Opus 를 쓴다. m4a 는 녹음이 끝날 때 moov 를 써야 완성되는데,
ffmpeg 가 그 전에 죽으면(SIGTERM, 강제 종료, 정전) 파일 전체를 못 읽게 된다.
Ogg 는 스트리밍 형식이라 중간에 끊겨도 그 지점까지 그대로 재생된다.
"""

import json
import math
import re
import signal
import subprocess
import tempfile
import time
from datetime import datetime
from pathlib import Path

DEVICE_HINT = "blackhole"          # 장치 이름에 이 문자열이 들어가면 BlackHole
SILENCE_DB = -80.0                 # 이보다 조용하면 무음으로 본다

# 녹음 중 ffmpeg 가 1초마다 stderr 로 뱉는 레벨. 완전 무음이면 -inf 로 나온다.
RMS_RE = re.compile(rb"lavfi\.astats\.Overall\.RMS_level=(-?[\d.]+|-inf)")


def _run(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, **kw)


def find_device():
    """avfoundation 오디오 장치 목록에서 BlackHole 의 번호와 이름을 찾는다."""
    out = _run(["ffmpeg", "-f", "avfoundation", "-list_devices", "true",
                "-i", ""]).stderr
    audio, found = False, []
    for line in out.splitlines():
        if "AVFoundation audio devices" in line:
            audio = True
            continue
        if "AVFoundation video devices" in line:
            audio = False
            continue
        if not audio:
            continue
        m = re.search(r"\[(\d+)\]\s+(.+?)\s*$", line)
        if m:
            found.append((int(m.group(1)), m.group(2)))
    for idx, name in found:
        if DEVICE_HINT in name.lower():
            return idx, name
    return None, None


def default_output():
    """현재 맥의 기본 출력 장치 이름."""
    try:
        data = json.loads(_run(["system_profiler", "SPAudioDataType",
                                "-json"]).stdout)
    except Exception:
        return None
    result = []

    def walk(items):
        for it in items:
            for v in it.values():
                if isinstance(v, list):
                    walk(v)
            if it.get("coreaudio_default_audio_output_device") == "spaudio_yes":
                result.append(it.get("_name"))
    walk(data.get("SPAudioDataType", []))
    return result[0] if result else None


def output_is_blackhole():
    """지금 출력으로 녹음하면 소리가 잡히는 상태인지."""
    name = default_output()
    if not name:
        return None, None          # 판단 불가
    low = name.lower()
    # 다중 출력 장치는 이름에 BlackHole 이 안 들어갈 수 있어 '다중/multi' 도 인정한다
    ok = DEVICE_HINT in low or "다중" in name or "multi" in low
    return ok, name


def is_readable(path):
    """받아쓰기에 넘기기 전에 파일이 정상인지 확인한다."""
    r = _run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
              "-of", "csv=p=0", str(path)])
    if r.returncode != 0 or not r.stdout.strip():
        return False, (r.stderr or "").strip().splitlines()[-1:] and \
            (r.stderr or "").strip().splitlines()[-1] or "읽을 수 없는 파일"
    try:
        return True, float(r.stdout.strip())
    except ValueError:
        return False, "길이를 읽지 못했습니다"


def measure_volume(path):
    """평균 볼륨(dB). 무음이면 -91 근처가 나온다."""
    out = _run(["ffmpeg", "-i", str(path), "-af", "volumedetect",
                "-f", "null", "-"]).stderr
    m = re.search(r"mean_volume:\s*(-?[\d.]+) dB", out)
    return float(m.group(1)) if m else None


def mean_db(levels):
    """dB 값들의 평균. dB 는 로그 눈금이라 그냥 더하면 안 된다.

    세기(power)로 되돌려 평균을 내고 다시 dB 로 바꾼다. 무음(-inf)은 세기 0 이
    되므로 자연스럽게 섞인다.
    """
    if not levels:
        return None
    m = sum(10 ** (db / 10) for db in levels) / len(levels)
    return 10 * math.log10(m) if m > 0 else float("-inf")


def default_dir():
    d = Path.home() / "Documents" / "녹음"
    d.mkdir(parents=True, exist_ok=True)
    return d


def new_name(dirpath=None):
    d = Path(dirpath) if dirpath else default_dir()
    return d / f"녹음_{datetime.now():%Y-%m-%d_%H-%M-%S}.ogg"


class Recorder:
    """ffmpeg 를 띄워 두고 stop() 할 때까지 녹음한다."""

    def __init__(self):
        self.proc = None
        self.path = None
        self.started = None
        self._log = None       # ffmpeg stderr 를 받아 둘 임시 파일
        self._log_pos = 0      # 레벨을 어디까지 읽었는지

    @property
    def running(self):
        return self.proc is not None and self.proc.poll() is None

    def start(self, path, device=None):
        if self.running:
            raise RuntimeError("이미 녹음 중입니다.")
        idx = device
        if idx is None:
            idx, name = find_device()
            if idx is None:
                raise RuntimeError(
                    "BlackHole 오디오 장치를 찾지 못했습니다.\n"
                    "설치되어 있는지 확인하세요.")
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)

        cmd = ["ffmpeg", "-y", "-nostats", "-f", "avfoundation", "-i", f":{idx}",
               "-map", "0:a",
               "-ar", "16000", "-ac", "1", "-c:a", "libopus", "-b:a", "24k",
               # 버퍼에 쌓아두지 말고 바로 파일에 쓴다.
               # 앱이나 맥이 갑자기 죽어도 그 시점까지 남는다.
               "-flush_packets", "1", "-page_duration", "1000000",
               str(path),
               # 두 번째 출력. 파일로는 아무것도 쓰지 않고 1초치씩 묶어
               # RMS 레벨만 stderr 로 뱉는다. 녹음 파일로 가는 위쪽 경로는
               # 건드리지 않으므로 녹음 자체에는 영향이 없다.
               # ametadata 의 file= 로 쓰면 종료할 때까지 버퍼에 갇혀서
               # 실시간으로 못 읽는다. 그래서 stderr 로 내보낸다.
               "-map", "0:a",
               "-af", ("aresample=16000,asetnsamples=16000,"
                       "astats=metadata=1:reset=1,"
                       "ametadata=print:key=lavfi.astats.Overall.RMS_level"),
               "-f", "null", "-"]
        # stderr 를 PIPE 로 두고 읽지 않으면 버퍼가 차는 순간 ffmpeg 가 멈춘다.
        # 파일로 받아 두면 막히지 않으면서 오류도 확인할 수 있다.
        self._log = tempfile.NamedTemporaryFile(
            mode="w+", suffix=".log", delete=False)
        self._log_pos = 0
        self.proc = subprocess.Popen(
            cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
            stderr=self._log)
        self.path = path
        self.started = time.time()

        # 곧바로 죽으면(권한 거부 등) 바로 알린다
        time.sleep(0.6)
        if self.proc.poll() is not None:
            err = self._read_log()
            self._cleanup_log()
            self.proc = None
            hint = ""
            if "Operation not permitted" in err or "denied" in err.lower():
                hint = ("\n\n마이크 권한이 필요합니다. 시스템 설정 → 개인정보 보호 및 "
                        "보안 → 마이크 에서 이 앱(터미널/Python)을 허용하세요.")
            raise RuntimeError(f"녹음을 시작하지 못했습니다.{hint}\n\n{err[-400:]}")
        return path

    def levels(self):
        """마지막 호출 이후 새로 나온 초당 RMS 레벨(dB). 무음은 -inf.

        로그는 녹음 내내 자라므로 매번 통째로 읽지 않고 읽던 자리에서 잇는다.
        """
        if self._log is None:
            return []
        try:
            with open(self._log.name, "rb") as f:
                f.seek(self._log_pos)
                chunk = f.read()
        except Exception:
            return []
        # 줄이 끊긴 채로 숫자를 잘라 읽지 않도록 마지막 줄바꿈까지만 쓴다.
        cut = chunk.rfind(b"\n")
        if cut < 0:
            return []
        self._log_pos += cut + 1
        return [float(m) for m in RMS_RE.findall(chunk[:cut])]

    def _read_log(self):
        try:
            self._log.flush()
            text = Path(self._log.name).read_text(errors="replace")
        except Exception:
            return ""
        # 레벨 측정 때문에 나오는 줄은 오류가 아니다. 걷어내지 않으면
        # 정작 봐야 할 오류 메시지가 뒤로 밀려난다.
        keep = [ln for ln in text.splitlines()
                if not ln.startswith("frame:") and "lavfi.astats" not in ln]
        return "\n".join(keep)[-800:]

    def _cleanup_log(self):
        if self._log is not None:
            try:
                self._log.close()
                Path(self._log.name).unlink(missing_ok=True)
            except Exception:
                pass
            self._log = None
        self._log_pos = 0

    @property
    def elapsed(self):
        return time.time() - self.started if self.started else 0.0

    def stop(self):
        """녹음을 끝내고 파일 경로를 돌려준다.

        ffmpeg 는 SIGTERM 을 받으면 파일 마무리를 건너뛰고 바로 죽는다.
        그래서 q → SIGINT 순으로 정상 종료를 유도하고, SIGKILL 은 마지막 수단이다.
        """
        if not self.running:
            raise RuntimeError("녹음 중이 아닙니다.")

        # 1) 정상 종료 요청
        try:
            self.proc.stdin.write(b"q")
            self.proc.stdin.flush()
            self.proc.stdin.close()
        except Exception:
            pass
        try:
            self.proc.wait(timeout=8)
        except subprocess.TimeoutExpired:
            # 2) Ctrl+C 와 같은 신호. ffmpeg 가 여기서도 마무리를 시도한다.
            try:
                self.proc.send_signal(signal.SIGINT)
                self.proc.wait(timeout=8)
            except Exception:
                # 3) 마지막 수단. Ogg 라 여기까지 와도 파일은 살아 있다.
                self.proc.kill()
                try:
                    self.proc.wait(timeout=5)
                except Exception:
                    pass

        path, self.proc, self.started = self.path, None, None
        self._cleanup_log()
        return path


if __name__ == "__main__":
    import sys
    idx, name = find_device()
    print(f"BlackHole 장치: [{idx}] {name}" if idx is not None else "BlackHole 없음")
    ok, out = output_is_blackhole()
    print(f"현재 기본 출력: {out}  →  {'녹음됨' if ok else '무음이 됩니다'}")
    secs = float(sys.argv[1]) if len(sys.argv) > 1 else 3.0
    r = Recorder()
    p = r.start(new_name())
    print(f"{secs}초 녹음 중… → {p}")
    time.sleep(secs)
    r.stop()
    vol = measure_volume(p)
    print(f"완료. 평균 볼륨 {vol} dB"
          + ("  ← 무음입니다" if vol is not None and vol < SILENCE_DB else ""))
