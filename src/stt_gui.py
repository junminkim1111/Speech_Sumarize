#!/usr/bin/env python3
"""녹음 받아쓰기 GUI.

시스템 소리를 녹음하고, 받아쓰고, 요약한다.
받아쓰기 엔진은 Gemini / Whisper(Groq API) / Whisper(local) 중에 고른다.
"""

import queue
import subprocess
import sys
import tempfile
import threading
import time
import traceback
from pathlib import Path
from types import SimpleNamespace

import tkinter as tk
from tkinter import filedialog, messagebox, ttk

try:
    from tkinterdnd2 import DND_FILES, TkinterDnD
    HAS_DND = True
except Exception:            # tkinterdnd2 미설치 환경에서도 그냥 돌아가게
    DND_FILES = None
    TkinterDnD = None
    HAS_DND = False

sys.path.insert(0, str(Path(__file__).resolve().parent))
import transcribe as T  # noqa: E402
import summarize as S  # noqa: E402
import gemini_stt as G  # noqa: E402
import local_stt as L  # noqa: E402
import recorder as R  # noqa: E402

# 오디오 + 이미 받아쓴 대본(.txt) 을 목록에 받는다.
ACCEPTED = T.AUDIO_EXTS | {".txt"}

# 엔진 이름. 비교에 쓰는 문자열이라 상수로 둔다.
# Whisper 는 Groq API 로, Whisper(local) 은 mlx-whisper 로 돌린다.
ENG_GEMINI = "Gemini"
ENG_GROQ = "Whisper"
ENG_LOCAL = "Whisper(local)"
ENGINES = [ENG_GEMINI, ENG_GROQ, ENG_LOCAL]


def _placeholder(entry, var, hint):
    """비어 있을 때 회색 안내를 보여주는 한 줄 입력칸."""
    def show():
        if not var.get():
            entry.configure(foreground="#999")
            var.set(hint)
            entry._ph = True

    def on_in(_):
        if getattr(entry, "_ph", False):
            var.set("")
            entry.configure(foreground="")
            entry._ph = False

    def on_out(_):
        show()

    entry.bind("<FocusIn>", on_in)
    entry.bind("<FocusOut>", on_out)
    show()


def _placeholder_text(widget, hint):
    """여러 줄 입력칸용 안내."""
    def show():
        if not widget.get("1.0", "end").strip():
            widget.insert("1.0", hint)
            widget.configure(foreground="#999")
            widget._ph = True

    def on_in(_):
        if getattr(widget, "_ph", False):
            widget.delete("1.0", "end")
            widget.configure(foreground="")
            widget._ph = False

    widget.bind("<FocusIn>", on_in)
    widget.bind("<FocusOut>", lambda _: show())
    show()


def read_placeholder(widget):
    """안내문이 떠 있는 상태면 빈 문자열로 취급한다."""
    if getattr(widget, "_ph", False):
        return ""
    if hasattr(widget, "get") and isinstance(widget, tk.Text):
        return widget.get("1.0", "end").strip()
    return ""


def move_to_trash(path):
    """파일을 휴지통으로 보낸다. 완전 삭제가 아니라 되돌릴 수 있다."""
    # Finder 는 절대 경로만 받는다. 상대 경로를 주면 -10010 으로 실패한다.
    full = Path(path).resolve()
    if not full.exists():
        return
    # AppleScript 문자열 규칙대로 이스케이프한다. 따옴표나 역슬래시가 든
    # 파일 이름이 스크립트로 해석되는 일이 없게 한다.
    esc = str(full).replace("\\", "\\\\").replace('"', '\\"')
    script = f'tell application "Finder" to delete POSIX file "{esc}"' 
    r = subprocess.run(["osascript", "-e", script],
                       capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError((r.stderr or "휴지통으로 옮기지 못했습니다").strip())


def human_size(n):
    return f"{n / 1e6:.1f}MB" if n >= 1e6 else f"{n / 1e3:.0f}KB"


def human_time(sec):
    m, s = divmod(int(sec), 60)
    h, m = divmod(m, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


class App:
    def __init__(self, root):
        self.root = root
        root.title("녹음 받아쓰기")

        self.files: list[Path] = []
        self.outdir: Path | None = None
        self.events = queue.Queue()
        self.rec = R.Recorder()
        self._rec_tick = None
        self.worker = None
        self.cancel = threading.Event()
        self.made: dict[Path, Path] = {}   # 오디오 → 이번에 만든 대본 경로

        self._build()
        self._on_engine()          # 기본 엔진에 맞춰 옵션 상태를 맞춘다
        self._fit_window()
        self._poll()
        self.root.after(200, self._check_key)

    def _fit_window(self):
        """내용이 요구하는 크기에 맞춰 창을 잡고 화면 가운데에 둔다.

        섹션이 늘거나 줄어도 잘리지 않도록 고정값 대신 계산해서 쓴다.
        """
        self.root.update_idletasks()
        need_w = self.root.winfo_reqwidth()
        need_h = self.root.winfo_reqheight()

        # 목록과 로그가 넉넉히 보이도록 여유를 준다
        w = max(860, need_w + 40)
        h = need_h + 120

        sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        w = min(w, sw - 80)
        h = min(h, sh - 120)          # 메뉴 막대와 Dock 을 피한다

        x, y = (sw - w) // 2, max(20, (sh - h) // 3)
        self.root.geometry(f"{w}x{h}+{x}+{y}")
        self.root.minsize(min(700, w), min(need_h, h))

    # ---------------- 화면 구성 ----------------
    def _build(self):
        pad = dict(padx=12, pady=(10, 0))

        # 1. 파일 선택
        f1 = ttk.LabelFrame(self.root, text="1. 녹음 파일")
        f1.pack(fill="both", expand=True, **pad)

        bar = ttk.Frame(f1)
        bar.pack(fill="x", padx=10, pady=(10, 6))
        ttk.Button(bar, text="파일 추가…", command=self.add_files).pack(side="left")
        ttk.Button(bar, text="폴더 추가…", command=self.add_folder).pack(side="left", padx=6)
        ttk.Button(bar, text="선택 제거", command=self.remove_sel).pack(side="left")
        ttk.Button(bar, text="전체 비우기", command=self.clear_files).pack(side="left", padx=6)
        self.count_lbl = ttk.Label(bar, text="0개")
        self.count_lbl.pack(side="right")

        rec = ttk.Frame(f1)
        rec.pack(fill="x", padx=10, pady=(0, 8))
        self.rec_btn = ttk.Button(rec, text="● 시스템 소리 녹음",
                                  command=self.toggle_record)
        self.rec_btn.pack(side="left")
        self.rec_lbl = ttk.Label(rec, text="", foreground="#777")
        self.rec_lbl.pack(side="left", padx=10)

        wrap = ttk.Frame(f1)
        wrap.pack(fill="both", expand=True, padx=10, pady=(0, 10))
        self.listbox = tk.Listbox(wrap, selectmode="extended", height=7,
                                  activestyle="none", highlightthickness=0)
        sb = ttk.Scrollbar(wrap, orient="vertical", command=self.listbox.yview)
        self.listbox.configure(yscrollcommand=sb.set)
        self.listbox.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")

        # 목록이 비어 있을 때 보이는 안내 문구
        hint = ("파일을 여기로 끌어다 놓으세요" if HAS_DND
                else "위의 '파일 추가…' 버튼으로 선택하세요")
        self.hint = tk.Label(self.listbox, text=hint, fg="#999",
                             bg=self.listbox.cget("bg"))
        self.hint.place(relx=0.5, rely=0.5, anchor="center")

        self._setup_dnd()

        # 2. 저장 위치
        f2 = ttk.LabelFrame(self.root, text="2. 저장 위치")
        f2.pack(fill="x", **pad)
        row = ttk.Frame(f2)
        row.pack(fill="x", padx=10, pady=10)
        ttk.Button(row, text="폴더 선택…", command=self.pick_outdir).pack(side="left")
        self.out_lbl = ttk.Label(row, text="원본 파일과 같은 폴더", foreground="#555")
        self.out_lbl.pack(side="left", padx=10)

        # 3. 옵션
        f3 = ttk.LabelFrame(self.root, text="3. 받아쓰기 생성")
        f3.pack(fill="x", **pad)
        o = ttk.Frame(f3)
        o.pack(fill="x", padx=10, pady=10)

        ttk.Label(o, text="엔진").grid(row=0, column=0, sticky="w")
        self.engine = tk.StringVar(value=ENG_LOCAL)
        ttk.Combobox(o, textvariable=self.engine, width=12, state="readonly",
                     values=ENGINES).grid(
            row=0, column=1, sticky="w", padx=(6, 18))
        self.engine.trace_add("write", lambda *_: self._on_engine())

        ttk.Label(o, text="언어").grid(row=0, column=2, sticky="w")
        self.lang = tk.StringVar(value="ko")
        ttk.Combobox(o, textvariable=self.lang, width=6, state="readonly",
                     values=["ko", "en", "ja", "zh"]).grid(
            row=0, column=3, sticky="w", padx=(6, 18))

        self.size = tk.StringVar(value=L.DEFAULT)
        self.size_box = ttk.Combobox(o, textvariable=self.size, width=8,
                                     state="disabled", values=list(L.MODELS))
        self.size_box.grid(row=0, column=4, sticky="w")

        # 모델은 엔진에 따라 정해진다. 비워두면 자동 선택.
        self.model = tk.StringVar(value="자동")

        ttk.Label(o, text="용어 힌트").grid(row=1, column=0, sticky="w", pady=(10, 0))
        self.prompt = tk.StringVar(value="")
        self.hint_entry = ttk.Entry(o, textvariable=self.prompt)
        self.hint_entry.grid(row=1, column=1, columnspan=4, sticky="ew",
                             padx=(6, 0), pady=(10, 0))
        _placeholder(self.hint_entry, self.prompt,
                     "자주 나오는 용어와 고유명사를 쉼표로 (예: RLPD, behavior cloning)")
        o.columnconfigure(4, weight=1)

        run1 = ttk.Frame(o)
        run1.grid(row=2, column=0, columnspan=5, sticky="w", pady=(12, 0))
        self.run_btn = ttk.Button(run1, text="받아쓰기 시작", command=self.start)
        self.run_btn.pack(side="left")
        self.skip_done = tk.BooleanVar(value=True)
        ttk.Checkbutton(run1, text="이미 받아쓴 파일은 건너뛰기",
                        variable=self.skip_done).pack(side="left", padx=12)

        # 4. 요약 생성
        f4s = ttk.LabelFrame(self.root, text="4. 요약 생성")
        f4s.pack(fill="x", **pad)
        sm = ttk.Frame(f4s)
        sm.pack(fill="both", padx=10, pady=10)

        ttk.Label(sm, text="추가 요청 (선택)").pack(anchor="w")
        self.extra = tk.Text(sm, height=3, wrap="word", highlightthickness=1,
                             highlightbackground="#ccc")
        self.extra.pack(fill="x", pady=(4, 0))
        _placeholder_text(
            self.extra,
            "비워두면 알아서 정리합니다. 원하는 게 있으면 적으세요.\n"
            "예: 할 일만 체크리스트로 / 영어로 써줘 / 3줄 안에")

        run2 = ttk.Frame(sm)
        run2.pack(fill="x", pady=(10, 0))
        self.sum_btn = ttk.Button(run2, text="요약 생성", command=self.start_summary)
        self.sum_btn.pack(side="left")

        ttk.Label(run2, text="저장 형식").pack(side="left", padx=(14, 4))
        self.fmt = tk.StringVar(value=".md")
        ttk.Combobox(run2, textvariable=self.fmt, width=8, state="readonly",
                     values=[".md", ".txt"]).pack(side="left")
        self.fmt.set(".md")

        ttk.Label(run2, text="위 목록의 대본을 요약합니다",
                  foreground="#777").pack(side="left", padx=10)

        # 진행 상황
        f4 = ttk.Frame(self.root)
        f4.pack(fill="both", expand=True, padx=12, pady=(10, 10))

        row = ttk.Frame(f4)
        row.pack(fill="x")
        self.stop_btn = ttk.Button(row, text="중지", command=self.stop,
                                   state="disabled")
        self.stop_btn.pack(side="left")
        self.status = ttk.Label(row, text="대기 중", foreground="#555")
        self.status.pack(side="left", padx=12)

        self.prog = ttk.Progressbar(f4, mode="determinate", maximum=100)
        self.prog.pack(fill="x", pady=(10, 6))

        self.log = tk.Text(f4, height=9, wrap="word", state="disabled",
                           highlightthickness=0, font=("Menlo", 11))
        lsb = ttk.Scrollbar(f4, orient="vertical", command=self.log.yview)
        self.log.configure(yscrollcommand=lsb.set)
        self.log.pack(side="left", fill="both", expand=True)
        lsb.pack(side="right", fill="y")
        self.log.tag_config("err", foreground="#c0392b")
        self.log.tag_config("ok", foreground="#1e8449")
        self.log.tag_config("dim", foreground="#777")

    def _on_engine(self):
        eng = self.engine.get()
        local = eng == ENG_LOCAL
        self.size_box.configure(state="readonly" if local else "disabled")
        if not local:
            self._log(f"엔진: {eng}", "dim")
            return
        if not L.available():
            self._log(f"{ENG_LOCAL} 엔진을 쓰려면 mlx-whisper 가 필요합니다: "
                      "pip install mlx-whisper", "err")
            messagebox.showwarning(
                "mlx-whisper 없음",
                f"{ENG_LOCAL} 로 받아쓰려면 mlx-whisper 를 설치해야 합니다.\n\n"
                "  pip install mlx-whisper\n\n"
                "Apple Silicon Mac 에서만 동작합니다.")
            self.engine.set(ENG_GEMINI)
            return
        ready = L.is_downloaded(self.size.get())
        self._log(f"엔진: {ENG_LOCAL} (인터넷·토큰 불필요)"
                  + ("" if ready else " — 첫 실행 때 모델을 내려받습니다 (1~2GB)"),
                  "dim")

    # ---------------- 녹음 ----------------
    def toggle_record(self):
        if self.rec.running:
            self._stop_record()
        else:
            self._start_record()

    def _start_record(self):
        idx, name = R.find_device()
        if idx is None:
            messagebox.showerror(
                "BlackHole 없음",
                "BlackHole 오디오 장치를 찾지 못했습니다.\n"
                "설치되어 있는지 확인하세요.")
            return

        # 출력이 BlackHole 이 아니면 통째로 무음이 된다. 미리 알린다.
        ok, out = R.output_is_blackhole()
        if ok is False:
            go = messagebox.askyesno(
                "출력 장치 확인",
                f"지금 맥의 출력이 '{out}' 입니다.\n\n"
                "이대로 녹음하면 소리가 하나도 안 잡힙니다.\n"
                "메뉴막대의 소리 아이콘에서 출력을 BlackHole 로 바꾼 뒤\n"
                "녹음하세요.\n\n그래도 시작할까요?")
            if not go:
                return

        dest = self.outdir if self.outdir else None
        path = R.new_name(dest)
        try:
            self.rec.start(path)
        except Exception as e:
            messagebox.showerror("녹음 시작 실패", str(e))
            self._log(f"녹음 시작 실패: {e}", "err")
            return

        self.rec_btn.config(text="■ 녹음 종료")
        self._log(f"녹음 시작 — {path}", "dim")
        self._tick()

    def _tick(self):
        if self.rec.running:
            self.rec_lbl.config(text=f"● 녹음 중  {human_time(self.rec.elapsed)}",
                                foreground="#c0392b")
            self._rec_tick = self.root.after(500, self._tick)
            return
        # 여기 왔다는 건 ffmpeg 가 스스로 죽었다는 뜻이다.
        # (장치가 빠졌거나 디스크가 찼거나…) 버튼이 '종료' 인 채로 굳어
        # 지금까지 녹음한 파일을 못 꺼내는 상황을 막는다.
        if self._rec_tick is not None:
            self._rec_tick = None
            self._log("녹음이 예기치 않게 끊겼습니다.", "err")
            err = self.rec._read_log()
            self.rec._cleanup_log()
            path, self.rec.proc, self.rec.started = self.rec.path, None, None
            self._finish_record(path, interrupted=True, err=err)

    def _stop_record(self):
        if self._rec_tick:
            self.root.after_cancel(self._rec_tick)
            self._rec_tick = None
        try:
            path = self.rec.stop()
        except Exception as e:
            messagebox.showerror("녹음 종료 실패", str(e))
            return
        self._finish_record(path)

    def _finish_record(self, path, interrupted=False, err=""):
        self.rec_btn.config(text="● 시스템 소리 녹음")
        self.rec_lbl.config(text="", foreground="#777")

        if path is None or not path.exists() or path.stat().st_size < 500:
            self._log("녹음 파일이 만들어지지 않았습니다.", "err")
            messagebox.showerror(
                "녹음 실패",
                "녹음 파일이 만들어지지 않았습니다."
                + (f"\n\n{err[-300:]}" if err else ""))
            return

        # 목록에 넣기 전에 실제로 읽히는 파일인지 확인한다.
        # 깨진 파일을 나중에 받아쓰기 단계에서 발견하면 녹음을 통째로 날린다.
        ok, info = R.is_readable(path)
        if not ok:
            self._log(f"녹음 파일이 손상됐습니다: {info}", "err")
            messagebox.showerror(
                "녹음 파일 손상",
                f"녹음이 정상적으로 저장되지 않았습니다.\n\n{info}\n\n"
                f"파일은 남겨 둡니다:\n{path}")
            return
        self._log(f"길이 {human_time(info)}", "dim")
        if interrupted:
            messagebox.showwarning(
                "녹음 중단됨",
                f"녹음이 도중에 끊겼습니다.\n\n"
                f"끊긴 지점까지는 정상적으로 저장했습니다 "
                f"({human_time(info)}).\n목록에 추가해 두겠습니다.")

        self._add([path])
        size = human_size(path.stat().st_size)
        self._log(f"녹음 완료 — {path.name} ({size}), 목록에 추가했습니다", "ok")

        # 무음이면 알려준다. 출력 전환을 잊는 사고가 잦다.
        self.status.config(text="녹음 확인 중…")
        self.root.update_idletasks()
        vol = R.measure_volume(path)
        self.status.config(text="대기 중")
        if vol is not None and vol < R.SILENCE_DB:
            self._log(f"경고: 녹음이 무음입니다 (평균 {vol:.0f} dB)", "err")
            messagebox.showwarning(
                "무음 녹음",
                f"녹음된 소리가 없습니다 (평균 {vol:.0f} dB).\n\n"
                "맥의 출력이 BlackHole 로 되어 있는지 확인하고\n"
                "다시 녹음하세요.")
        elif vol is not None:
            self._log(f"평균 볼륨 {vol:.0f} dB", "dim")

    # ---------------- 파일 목록 ----------------
    def _setup_dnd(self):
        """창 어디에 떨어뜨려도 받도록 등록한다."""
        if not HAS_DND:
            return
        for w in (self.listbox, self.root):
            try:
                w.drop_target_register(DND_FILES)
                w.dnd_bind("<<Drop>>", self._on_drop)
                w.dnd_bind("<<DropEnter>>", self._drag_enter)
                w.dnd_bind("<<DropLeave>>", self._drag_leave)
            except Exception:
                pass

    def _drag_enter(self, event):
        self.listbox.configure(highlightthickness=2, highlightbackground="#0a84ff",
                               highlightcolor="#0a84ff")
        self.hint.configure(text="놓으면 목록에 추가됩니다", fg="#0a84ff")
        return event.action

    def _drag_leave(self, event):
        self.listbox.configure(highlightthickness=0)
        self._refresh_hint()
        return event.action

    def _on_drop(self, event):
        self._drag_leave(event)
        found, skipped = [], 0
        for raw in self.root.tk.splitlist(event.data):
            p = Path(raw)
            if p.is_dir():
                inner = [f for f in sorted(p.rglob("*"))
                         if f.is_file() and f.suffix.lower() in ACCEPTED]
                if inner:
                    found += inner
                else:
                    skipped += 1
            elif p.suffix.lower() in ACCEPTED:
                found.append(p)
            else:
                skipped += 1

        added = self._add(found)
        msg = f"드롭: {added}개 추가"
        if added < len(found):
            msg += f" (중복 {len(found) - added}개 제외)"
        if skipped:
            msg += f", 오디오가 아닌 항목 {skipped}개 무시"
        self._log(msg, "dim")
        return event.action

    def _refresh_hint(self):
        """목록이 비었을 때만 안내를 보여준다."""
        base = ("파일을 여기로 끌어다 놓으세요" if HAS_DND
                else "위의 '파일 추가…' 버튼으로 선택하세요")
        self.hint.configure(text=base, fg="#999")
        if self.files:
            self.hint.place_forget()
        else:
            self.hint.place(relx=0.5, rely=0.5, anchor="center")

    def _add(self, paths):
        added = 0
        for p in paths:
            if p not in self.files:
                self.files.append(p)
                self.listbox.insert("end", f"  {p.name}   ({human_size(p.stat().st_size)})")
                added += 1
        self._refresh_count()
        return added

    def _refresh_count(self):
        self.count_lbl.config(text=f"{len(self.files)}개")
        self._refresh_hint()

    def add_files(self):
        picked = filedialog.askopenfilenames(
            title="녹음 파일 선택",
            filetypes=[("오디오/비디오", "*.m4a *.mp3 *.wav *.flac *.ogg *.opus "
                                       "*.webm *.mp4 *.mov *.mkv *.aac *.amr"),
                       ("대본 (.txt)", "*.txt"),
                       ("모든 파일", "*.*")])
        if picked:
            self._add([Path(p) for p in picked])

    def add_folder(self):
        d = filedialog.askdirectory(title="녹음 파일이 있는 폴더 선택")
        if not d:
            return
        found = [f for f in sorted(Path(d).iterdir())
                 if f.suffix.lower() in ACCEPTED]
        if not found:
            messagebox.showinfo("없음", "그 폴더에 오디오 파일이 없습니다.")
            return
        self._add(found)

    def remove_sel(self):
        for i in sorted(self.listbox.curselection(), reverse=True):
            self.listbox.delete(i)
            del self.files[i]
        self._refresh_count()

    def clear_files(self):
        self.listbox.delete(0, "end")
        self.files.clear()
        self._refresh_count()

    def pick_outdir(self):
        d = filedialog.askdirectory(title="텍스트를 저장할 폴더 선택")
        if d:
            self.outdir = Path(d)
            self.out_lbl.config(text=str(self.outdir), foreground="#000")

    # ---------------- 로그/이벤트 ----------------
    def _log(self, msg, tag=None):
        self.log.configure(state="normal")
        self.log.insert("end", msg + "\n", tag or ())
        self.log.see("end")
        self.log.configure(state="disabled")

    def _poll(self):
        try:
            while True:
                kind, payload = self.events.get_nowait()
                if kind == "log":
                    self._log(payload[0], payload[1])
                elif kind == "progress":
                    self.prog["value"] = payload
                elif kind == "status":
                    self.status.config(text=payload)
                elif kind == "done":
                    self._finish(payload)
                elif kind == "cleanup":
                    self._ask_cleanup(payload)
                elif kind == "made":
                    self.made[payload[0]] = payload[1]
        except queue.Empty:
            pass
        self.root.after(80, self._poll)

    def emit(self, kind, payload):
        self.events.put((kind, payload))

    def _check_key(self):
        """어떤 키가 준비됐는지 알려만 준다. 시작을 막지는 않는다.

        기본 엔진이 Whisper(local) 이라 키가 없어도 받아쓰기는 된다.
        Gemini 키는 요약할 때 필요하고, Groq 키는 Whisper 엔진에서만 쓴다.
        """
        try:
            S.load_gemini_key()
            self._log("Gemini 키 확인됨 (요약 가능)", "dim")
        except Exception:
            self._log("Gemini 키가 없어 요약을 쓸 수 없습니다. "
                      "~/.gemini_key 에 저장하세요.", "err")
        try:
            T.load_api_key()
        except Exception:
            self._log(f"Groq 키 없음 — {ENG_GROQ} 엔진은 쓸 수 없습니다 "
                      f"({ENG_LOCAL} 은 키 없이 동작).", "dim")

        self._log("파일을 끌어다 놓거나 '파일 추가…'로 선택하세요."
                  if HAS_DND else
                  "'파일 추가…'로 파일을 선택하세요. "
                  "(드래그앤드롭은 pip install tkinterdnd2 필요)", "dim")

    # ---------------- 실행 ----------------
    def _hint(self):
        """용어 힌트. 안내문이 떠 있으면 빈 값으로 본다."""
        if getattr(self.hint_entry, "_ph", False):
            return ""
        return self.prompt.get().strip()

    def _busy(self, on):
        self.run_btn.config(state="disabled" if on else "normal")
        self.sum_btn.config(state="disabled" if on else "normal")
        self.stop_btn.config(state="normal" if on else "disabled")

    def start(self):
        if not self.files:
            messagebox.showinfo("파일 없음", "받아쓸 녹음 파일을 먼저 추가하세요.")
            return
        engine = self.engine.get()
        key = None
        if engine != ENG_LOCAL:
            try:
                key = (S.load_gemini_key() if engine == ENG_GEMINI
                       else T.load_api_key())
            except Exception as e:
                messagebox.showerror("API 키 오류", str(e))
                return

        self.cancel.clear()
        self._busy(True)
        self.prog["value"] = 0
        args = SimpleNamespace(
            lang=self.lang.get(),
            model=("whisper-large-v3" if engine == ENG_GROQ else None),
            prompt=self._hint(),
            size=self.size.get())
        args.engine = engine
        args.skip_done = self.skip_done.get()
        self.worker = threading.Thread(
            target=self._run, args=(key, list(self.files), self.outdir, args),
            daemon=True)
        self.worker.start()

    def start_summary(self):
        """목록의 대본(.txt)들을 요약한다. 오디오면 그 옆의 .txt 를 찾는다."""
        if not self.files:
            messagebox.showinfo("파일 없음", "요약할 대상을 먼저 추가하세요.")
            return
        try:
            gkey = S.load_gemini_key()
        except Exception as e:
            messagebox.showerror("Gemini 키 오류", str(e))
            return

        # (대본, 원본 오디오 or None) 쌍으로 들고 다닌다. 나중에 정리할 때 쓴다.
        targets, missing = [], []
        for f in self.files:
            if f.suffix.lower() == ".txt":
                targets.append((f, None))
                continue
            dest = self.outdir or f.parent
            cand = dest / (f.stem + ".txt")
            # 이번 실행에서 만든 대본을 기억해 두면 이름 충돌로 회의_2.txt 로
            # 저장된 경우에도 올바른 짝을 찾는다.
            cand = self.made.get(f, cand)
            if cand.exists():
                targets.append((cand, f))
            else:
                missing.append(cand)

        if missing:
            self._log(f"대본이 없어 건너뜁니다 ({len(missing)}개): "
                      + ", ".join(m.name for m in missing[:3])
                      + ("…" if len(missing) > 3 else ""), "dim")
        if not targets:
            messagebox.showinfo(
                "대본 없음",
                "요약할 .txt 가 없습니다.\n"
                "먼저 '받아쓰기 시작'을 누르거나, 대본 파일을 목록에 넣으세요.")
            return

        self.cancel.clear()
        self._busy(True)
        self.prog["value"] = 0
        self.worker = threading.Thread(
            target=self._run_summary,
            args=(targets, self.outdir, gkey, read_placeholder(self.extra),
                  self._hint(), self.fmt.get().lstrip(".")), daemon=True)
        self.worker.start()

    def _run_summary(self, files, outdir, gkey, extra, hint, fmt="md"):
        n = len(files)
        ok = fail = 0
        t0 = time.time()
        cleanup = []          # 요약에 성공한 건들의 (대본, 오디오)
        for i, (src, audio) in enumerate(files):
            if self.cancel.is_set():
                self.emit("log", (f"중지됨. {i}개까지 처리했습니다.", "dim"))
                break
            self.emit("status", f"{i + 1}/{n} — {src.name}")
            self.emit("log", (f"\n[{i + 1}/{n}] {src.name}", None))
            self.emit("progress", i / n * 100)
            try:
                body = src.read_text(encoding="utf-8")
                md = S.summarize(body, extra=extra, hint=hint, api_key=gkey,
                                 fmt=fmt,
                                 log=lambda m: self.emit("log", (f"    {m}", "dim")))
                dest = outdir or src.parent
                dest.mkdir(parents=True, exist_ok=True)
                mp = dest / (src.stem + "_요약." + fmt)
                mp.write_text(md, encoding="utf-8")
                self.emit("log", (f"    ✓ {mp}  ({len(md):,}자)", "ok"))
                cleanup.append((src, audio))
                ok += 1
            except Exception as e:
                fail += 1
                self.emit("log", (f"    ✗ 실패: {e}", "err"))
            self.emit("progress", (i + 1) / n * 100)
        self.emit("done", (ok, fail, 0, 0.0, time.time() - t0))
        # 요약이 성공한 것에 한해서만 정리를 제안한다
        if cleanup and not self.cancel.is_set():
            self.emit("cleanup", cleanup)

    def stop(self):
        self.cancel.set()
        self.emit("status", "중지하는 중… (현재 파일까지만 마칩니다)")

    def _run(self, key, files, outdir, args):
        engine = getattr(args, "engine", ENG_GROQ)
        gemini, local = engine == ENG_GEMINI, engine == ENG_LOCAL
        client = None
        if not gemini and not local:
            from groq import Groq
            client = Groq(api_key=key)
        n = len(files)
        ok = fail = skipped = 0
        total_audio = 0.0
        t0 = time.time()
        used = set()      # 이번 실행에서 이미 쓴 출력 경로

        for i, src in enumerate(files):
            if self.cancel.is_set():
                self.emit("log", (f"중지됨. {i}개까지 처리했습니다.", "dim"))
                break

            if src.suffix.lower() == ".txt":
                self.emit("log", (f"\n[{i + 1}/{n}] {src.name} — 대본이므로 건너뜁니다", "dim"))
                skipped += 1
                self.emit("progress", (i + 1) / n * 100)
                continue

            base = i / n * 100
            self.emit("status", f"{i + 1}/{n} — {src.name}")
            self.emit("log", (f"\n[{i + 1}/{n}] {src.name}", None))
            self.emit("progress", base)

            try:
                dest = outdir or src.parent
                dest.mkdir(parents=True, exist_ok=True)

                # 다른 폴더의 같은 이름 파일이 서로를 덮어쓰지 않도록 한다
                txt = dest / (src.stem + ".txt")
                if txt in used:
                    k = 2
                    while (dest / f"{src.stem}_{k}.txt") in used:
                        k += 1
                    txt = dest / f"{src.stem}_{k}.txt"
                    self.emit("log",
                              (f"    같은 이름이 있어 {txt.name} 으로 저장합니다", "dim"))
                used.add(txt)

                if getattr(args, "skip_done", False) and txt.exists():
                    self.emit("log", (f"    건너뜀 — {txt.name} 이미 있음", "dim"))
                    skipped += 1
                    self.emit("progress", (i + 1) / n * 100)
                    continue

                if local:
                    body_txt = L.transcribe(
                        src, lang=args.lang, hint=args.prompt, size=args.size,
                        log=lambda m: self.emit("log", (f"    {m}", "dim")))
                    texts, offset = [body_txt], 0.0
                    self.emit("progress", base + 100 / n)
                elif gemini:
                    self.emit("log", ("    전송 중…", "dim"))
                    body_txt = G.transcribe(
                        src, lang=args.lang, hint=args.prompt, api_key=key,
                        log=lambda m: self.emit("log", (f"    {m}", "dim")))
                    texts, offset = [body_txt], 0.0
                    self.emit("progress", base + 100 / n)
                else:
                    with tempfile.TemporaryDirectory() as tmp:
                        chunks = T.prepare(src, Path(tmp),
                                           log=lambda m: self.emit("log", (f"    {m}", "dim")))
                        texts, offset = [], 0.0
                        for k, chunk in enumerate(chunks):
                            if self.cancel.is_set():
                                raise RuntimeError("사용자가 중지했습니다")
                            if len(chunks) > 1:
                                self.emit("log", (f"    조각 {k + 1}/{len(chunks)} 전송 중…", "dim"))
                                self.emit("status",
                                          f"{i + 1}/{n} — {src.name} (조각 {k + 1}/{len(chunks)})")
                            else:
                                self.emit("log", ("    전송 중…", "dim"))
                            text, _, dur = T.transcribe_one(client, chunk, args, offset)
                            texts.append(text.strip())
                            offset += dur
                            self.emit("progress", base + (k + 1) / len(chunks) * (100 / n))

                total_audio += offset
                body = "\n".join(t for t in texts if t)
                txt.write_text(body, encoding="utf-8")
                dur_note = f", 녹음 {human_time(offset)}" if offset else ""
                self.emit("log",
                          (f"    ✓ {txt}  ({len(body):,}자{dur_note})", "ok"))
                self.emit("made", (src, txt))


                ok += 1

            except Exception as e:
                fail += 1
                self.emit("log", (f"    ✗ 실패: {e}", "err"))
                traceback.print_exc()

            self.emit("progress", (i + 1) / n * 100)

        self.emit("done", (ok, fail, skipped, total_audio, time.time() - t0))

    def _ask_cleanup(self, pairs):
        """요약이 끝난 뒤, 대본과 녹음 파일을 치울지 물어본다."""
        victims = []
        for txt, audio in pairs:
            for f in (txt, audio):
                if f and f.exists() and f not in victims:
                    victims.append(f)
        if not victims:
            return

        total = sum(f.stat().st_size for f in victims)
        shown = "\n".join(f"  · {f.name}" for f in victims[:12])
        if len(victims) > 12:
            shown += f"\n  … 외 {len(victims) - 12}개"

        if not messagebox.askyesno(
                "원본 정리",
                f"요약이 끝났습니다. 요약본(.md)은 남기고\n"
                f"아래 {len(victims)}개 파일({human_size(total)})을 "
                f"휴지통으로 보낼까요?\n\n{shown}\n\n"
                "완전 삭제가 아니라 휴지통으로 가므로 되돌릴 수 있습니다.",
                default="no"):
            self._log("원본을 그대로 두었습니다.", "dim")
            return

        moved = failed = 0
        for f in victims:
            try:
                move_to_trash(f)
                moved += 1
                if f in self.files:
                    i = self.files.index(f)
                    self.listbox.delete(i)
                    del self.files[i]
            except Exception as e:
                failed += 1
                self._log(f"  {f.name} 옮기지 못함: {e}", "err")
        self._refresh_count()
        self._log(f"휴지통으로 {moved}개 이동"
                  + (f", {failed}개 실패" if failed else ""),
                  "ok" if not failed else "err")

    def _finish(self, payload):
        ok, fail, skipped, audio, elapsed = payload
        self._busy(False)
        tail = f", 건너뜀 {skipped}" if skipped else ""
        self.status.config(text=f"완료 — 성공 {ok}, 실패 {fail}{tail}")
        msg = f"\n끝났습니다. 성공 {ok} / 실패 {fail}{tail}."
        if audio:
            msg += f" 녹음 {human_time(audio)} 분량."
        msg += f" 총 {human_time(elapsed)} 소요."
        self._log(msg, "ok" if not fail else "err")
        if ok and not fail:
            self.prog["value"] = 100


def main():
    root = TkinterDnD.Tk() if HAS_DND else tk.Tk()
    try:  # macOS 에서 조금 더 네이티브하게
        ttk.Style().theme_use("aqua")
    except Exception:
        pass
    app = App(root)

    def on_close():
        if app.rec.running:
            if not messagebox.askyesno("녹음 중",
                                       "녹음이 진행 중입니다. 중단하고 종료할까요?"):
                return
            try:
                app.rec.stop()
            except Exception:
                pass
        root.destroy()

    root.protocol("WM_DELETE_WINDOW", on_close)
    root.mainloop()


if __name__ == "__main__":
    main()
