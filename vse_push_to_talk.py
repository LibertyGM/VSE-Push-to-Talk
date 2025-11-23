bl_info = {
    "name": "VSE Push-to-Talk (Header+Progress, Channel-aware, Formats, Timer)",
    "author": "Konstantin Liberty",
    "version": (1, 0, 0),
    "blender": (4, 0, 0),
    "location": "Video Sequence Editor > Header (центр) + Sidebar: Audio > Push-to-Talk",
    "description": "Mic record to project folder, insert in...strip's channel, choose format, show on-timeline progress bar.",
    "category": "Sequencer",
}

import bpy
import os
import shutil
import subprocess
import time
from datetime import datetime

# ------- Global state -------
_REC = {
    "proc": None,
    "filepath": None,
    "frame_start": None,
    "channel": None,
    "backend": "pulse",
    "device": "default",
    "t0": 0.0,
    # >>> placeholder
    "placeholder_name": None,
    "scene_name": None,
}

def project_base_dir():
    base = bpy.path.abspath("//")
    if not base or base == "//":
        base = os.path.expanduser("~/BlenderRecordings")
        os.makedirs(base, exist_ok=True)
    return base

def unique_out_path(ext):
    ts = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    base = project_base_dir()
    return os.path.join(base, f"mic_record_{ts}.{ext}")

def ensure_ffmpeg():
    return shutil.which("ffmpeg") is not None

def _iter_sequences(se):
    """Safely iterate over all sequences for different Blender versions."""
    if se is None:
        return []
    seqs = getattr(se, "sequences_all", None)
    if seqs is None:
        seqs = getattr(se, "sequences", [])
    return seqs

def active_strip(context):
    se = context.scene.sequence_editor
    if se and se.active_strip:
        return se.active_strip
    if se:
        for s in se.strips_all:
            if getattr(s, "select", False):
                return s
    return None

def compute_insert_point(context, channel_hint=None):
    scn = context.scene
    cur = scn.frame_current
    ch = channel_hint
    a = active_strip(context)
    if a:
        ch = a.channel
        cur = max(cur, a.frame_final_end + 1)
    if ch is None:
        ch = 1
    se = scn.sequence_editor
    if se:
        while True:
            overlap = False
            for s in se.strips_all:
                if s.channel == ch:
                    if not (cur >= s.frame_final_end or (cur + 1) <= s.frame_final_start):
                        cur = s.frame_final_end + 1
                        overlap = True
                        break
            if not overlap:
                break
    return cur, ch

def add_sound_strip(context, filepath, frame_start, channel):
    scn = context.scene
    if scn.sequence_editor is None:
        scn.sequence_editor_create()
    seq = scn.sequence_editor
    name = os.path.basename(filepath)
    strip = seq.strips.new_sound(
        name=name,
        filepath=filepath,
        channel=channel,
        frame_start=frame_start,
    )
    return strip

def ffmpeg_cmd(backend, device, fmt):
    fmt = fmt.lower()
    if fmt == "wav":
        ext, codec, extra = "wav", ["-c:a", "pcm_s16le"], []
    elif fmt == "flac":
        ext, codec, extra = "flac", ["-c:a", "flac"], []
    elif fmt == "ogg":
        ext, codec, extra = "ogg", ["-c:a", "libvorbis"], ["-q:a", "5"]
    elif fmt == "mp3":
        ext, codec, extra = "mp3", ["-c:a", "libmp3lame"], ["-b:a", "192k"]
    else:
        ext, codec, extra = "wav", ["-c:a", "pcm_s16le"], []

    if backend not in {"pulse", "alsa"}:
        backend = "pulse"

    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel", "warning",
        "-y",
        "-f", backend,
        "-i", device,
        "-ac", "1",
        "-ar", "48000",
        *codec,
        *extra,
    ]
    return cmd, ext

# ---------------- Placeholder strip (progress bar) ----------------
def _get_placeholder(context):
    """Find placeholder strip by stored name (safe across redraws)."""
    name = _REC.get("placeholder_name")
    if not name:
        return None
    se = context.scene.sequence_editor
    if not se:
        return None
    for s in se.strips_all:
        if s.name == name:
            return s
    return None

def placeholder_create(context, frame_start, channel):
    scn = context.scene
    if scn.sequence_editor is None:
        scn.sequence_editor_create()
    seq = scn.sequence_editor

    name = f"__REC_PROGRESS_{int(time.time())}"
    strip = seq.strips.new_effect(
        name=name,
        type='COLOR',
        channel=channel,
        frame_start=frame_start,
        length=1,   # вместо frame_end=frame_start + 1
    )

    # Цвет для различимости на таймлайне
    try:
        strip.color = (0.9, 0.2, 0.2)
    except Exception:
        pass

    # 👉 Ключ: делаем заглушку невидимой в превью
    try:
        strip.mute = True
    except Exception:
        pass
    try:
        strip.blend_type = 'ALPHA_OVER'
        strip.blend_alpha = 0.0
    except Exception:
        pass

    _REC["placeholder_name"] = strip.name
    _REC["scene_name"] = scn.name
    return strip

def placeholder_update(context, frame_start, elapsed_sec):
    """Extend the placeholder to current elapsed length."""
    strip = _get_placeholder(context)
    if not strip:
        return
    fps = context.scene.render.fps / context.scene.render.fps_base
    length_frames = max(1, int(elapsed_sec * fps))
    # Для COLOR-strip мы можем менять frame_end
    new_end = frame_start + length_frames
    try:
        strip.frame_final_end = new_end  # read-only обычно — поэтому fallback ниже
    except Exception:
        try:
            strip.frame_end = new_end
        except Exception:
            # как запасной вариант — сдвиг через still_end
            strip.frame_still_end = max(0, length_frames - 1)

def placeholder_remove(context):
    strip = _get_placeholder(context)
    if strip:
        se = context.scene.sequence_editor
        try:
            se.strips.remove(strip)
        except Exception:
            pass
    _REC["placeholder_name"] = None

def _proc_running():
    p = _REC.get("proc")
    return (p is not None) and (p.poll() is None)

# ---------------- Recording control ----------------
def start_recording(context):
    props = context.window_manager.vse_ptt
    if not ensure_ffmpeg():
        raise RuntimeError("ffmpeg не найден. Установите: sudo pacman -S ffmpeg")

    # === ЗАЩИТА ОТ ДВОЙНОГО ЗАПУСКА ===
    if props.is_recording or _proc_running():
        raise RuntimeError("Запись уже идёт.")

    # если вдруг остался «труп» процесса из прошлой сессии — подчистим
    old = _REC.get("proc")
    if old is not None and old.poll() is not None:
        _REC["proc"] = None

    cmd, ext = ffmpeg_cmd(props.backend, props.device, props.format)
    out_path = unique_out_path(ext)
    os.makedirs(os.path.dirname(out_path), exist_ok=True)

    frame_start, channel = compute_insert_point(context)

    proc = subprocess.Popen(
        [*cmd, out_path],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=False
    )

    _REC.update({
        "proc": proc,
        "filepath": out_path,
        "frame_start": frame_start,
        "channel": channel,
        "backend": props.backend,
        "device": props.device,
        "t0": time.time(),
    })

    # >>> create placeholder bar
    placeholder_create(context, frame_start, channel)

    props.is_recording = True
    props.last_file = ""
    props.elapsed = 0.0
    bpy.ops.sequencer.ptt_timer_modal('INVOKE_DEFAULT')

def stop_recording(context):
    props = context.window_manager.vse_ptt
    proc = _REC.get("proc")
    if not proc:
        return None

    try:
        if proc.stdin and not proc.stdin.closed:
            proc.stdin.write(b"q")
            proc.stdin.flush()
    except Exception:
        pass

    try:
        proc.wait(timeout=5)
    except Exception:
        try:
            proc.terminate()
            proc.wait(timeout=3)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass

    _REC["proc"] = None
    filepath = _REC.get("filepath")
    frame_start = _REC.get("frame_start")
    channel = _REC.get("channel")
    _REC["filepath"] = None

    # >>> remove placeholder before inserting the real audio
    placeholder_remove(context)

    props.is_recording = False

    if filepath and os.path.exists(filepath):
        strip = add_sound_strip(context, filepath, frame_start, channel)
        props.last_file = bpy.path.relpath(filepath)
        return strip, filepath
    return None

def format_time(sec):
    sec = int(sec)
    m, s = divmod(sec, 60)
    h, m = divmod(m, 60)
    return f"{h:02d}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"

# -------- Properties & UI --------

class VSE_PTT_Props(bpy.types.PropertyGroup):
    is_recording: bpy.props.BoolProperty(default=False, name="Recording")
    backend: bpy.props.EnumProperty(
        name="Backend",
        items=[("pulse","PulseAudio/PipeWire","-f pulse (default)"),
               ("alsa","ALSA","-f alsa")],
        default="pulse")
    device: bpy.props.StringProperty(name="Input Device", default="default")
    format: bpy.props.EnumProperty(
        name="Format",
        items=[
            ("wav", "WAV (PCM 16-bit)", ""),
            ("flac", "FLAC (lossless)", ""),
            ("ogg", "OGG Vorbis", ""),
            ("mp3", "MP3 (192 kbps)", ""),
        ],
        default="wav")
    last_file: bpy.props.StringProperty(name="Last File", default="", subtype='FILE_PATH')
    elapsed: bpy.props.FloatProperty(name="Elapsed", default=0.0, precision=2)

class VSE_PTT_OT_Toggle(bpy.types.Operator):
    bl_idname = "sequencer.ptt_toggle_record"
    bl_label = "Start / Stop Recording"
    bl_description = "Record mic and insert into selected strip's channel"

    def execute(self, context):
        props = context.window_manager.vse_ptt
        if not props.is_recording:
            try:
                start_recording(context)
                self.report({'INFO'}, "Recording started…")
            except Exception as e:
                props.is_recording = False
                self.report({'ERROR'}, str(e))
                return {'CANCELLED'}
        else:
            try:
                res = stop_recording(context)
                if res:
                    _, fp = res
                    self.report({'INFO'}, f"Saved: {bpy.path.relpath(fp)}")
                else:
                    self.report({'WARNING'}, "Stopped, but no file created.")
            except Exception as e:
                self.report({'ERROR'}, str(e))
                return {'CANCELLED'}
        return {'FINISHED'}

class VSE_PTT_OT_TimerModal(bpy.types.Operator):
    bl_idname = "sequencer.ptt_timer_modal"
    bl_label = "PTT Timer Modal"
    _timer = None

    def modal(self, context, event):
        props = context.window_manager.vse_ptt
        if event.type == 'TIMER':
            if props.is_recording and _REC.get("t0", 0.0) > 0:
                props.elapsed = max(0.0, time.time() - _REC["t0"])
                # >>> grow placeholder according to elapsed
                try:
                    placeholder_update(context, _REC.get("frame_start") or context.scene.frame_current, props.elapsed)
                except Exception:
                    pass
                # redraw header & timeline
                for area in context.window.screen.areas:
                    if area.type == 'SEQUENCE_EDITOR':
                        area.tag_redraw()
            else:
                self.cancel(context)
                return {'CANCELLED'}
        return {'PASS_THROUGH'}

    def execute(self, context):
        wm = context.window_manager
        self._timer = wm.event_timer_add(0.05, window=context.window)
        wm.modal_handler_add(self)
        return {'RUNNING_MODAL'}

    def cancel(self, context):
        wm = context.window_manager
        if self._timer:
            wm.event_timer_remove(self._timer)
            self._timer = None

class VSE_PTT_PT_SidePanel(bpy.types.Panel):
    bl_space_type = "SEQUENCE_EDITOR"
    bl_region_type = "UI"
    bl_category = "Audio"
    bl_label = "Push-to-Talk"

    def draw(self, context):
        layout = self.layout
        p = context.window_manager.vse_ptt
        col = layout.column(align=True)
        col.prop(p, "backend")
        col.prop(p, "device")
        col.prop(p, "format")
        layout.separator()
        if p.is_recording:
            row = layout.row()
            row.label(text=f"● REC {format_time(p.elapsed)}")
            layout.operator("sequencer.ptt_toggle_record", text="Stop Recording", icon="REC")
        else:
            layout.operator("sequencer.ptt_toggle_record", text="Start Recording", icon="OUTLINER_DATA_SPEAKER")
        if p.last_file:
            layout.separator()
            layout.prop(p, "last_file", text="Last File")

def ptt_draw_header(self, context):
    space = context.space_data
    if not space or space.type != 'SEQUENCE_EDITOR':
        return
    layout = self.layout
    p = context.window_manager.vse_ptt
    layout.separator_spacer()
    row = layout.row()
    row.alignment = 'CENTER'
    if getattr(p, "is_recording", False):
        row.label(text=f"● REC {format_time(p.elapsed)}")
        row.operator("sequencer.ptt_toggle_record", text="Stop", icon="REC")
    else:
        row.operator("sequencer.ptt_toggle_record", text="Start", icon="OUTLINER_DATA_SPEAKER")
    layout.separator_spacer()

# -------- Register / Unregister --------
classes = (
    VSE_PTT_Props,
    VSE_PTT_OT_Toggle,
    VSE_PTT_OT_TimerModal,
    VSE_PTT_PT_SidePanel,
)

def register():
    for c in classes:
        bpy.utils.register_class(c)
    bpy.types.WindowManager.vse_ptt = bpy.props.PointerProperty(type=VSE_PTT_Props)
    bpy.types.SEQUENCER_HT_header.append(ptt_draw_header)

def unregister():
    try:
        bpy.types.SEQUENCER_HT_header.remove(ptt_draw_header)
    except Exception:
        pass
    for c in reversed(classes):
        bpy.utils.unregister_class(c)
    del bpy.types.WindowManager.vse_ptt

if __name__ == "__main__":
    register()
