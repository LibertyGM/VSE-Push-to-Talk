# VSE Push-to-Talk Recorder for Blender
**VSE Push-to-Talk** is a Blender add-on that allows you to record audio directly inside the Video Sequence Editor using a simple push-to-talk workflow.  
**No need for external audio software** − just press the button, speak, and the audio strip appears exactly where you need it.

**✨ Key Features**

• 🎤 Record microphone audio directly in VSE using FFmpeg  
• ⏺ Push-to-talk button in the VSE header  
• 🧩 Automatically inserts the recorded audio strip into the first free channel  
• 📍 Strip is aligned to the exact current frame  
• 📂 Custom output path for recorded files  
• 🟦 Temporary visual “recording indicator” strip while capturing  
• 🎚 Choose FFmpeg audio format (WAV, FLAC, OGG, MP3)  
• 🐧 Linux-ready (PipeWire / PulseAudio / ALSA support)  
• 🔧 Fully integrated into Blender’s VSE UI

### Why This Add-on Exists
Recording a voice-over or quick commentary in Blender normally requires external software.  
With this add-on, you can capture audio directly inside Blender, keeping your entire editing workflow in one place — faster, cleaner, and more convenient.

### How It Works
1. Move the playhead to the desired position
2. Press Start Recording
3. Speak
4. Press Stop Recording
5. The recorded audio strip appears in the VSE automatically

Behind the scenes the add-on calls FFmpeg and writes the audio to the selected folder before inserting it into your sequence.

**Requirements**  

• Blender 5.0 and higher  
• FFmpeg installed and available in system PATH  
• Linux (PulseAudio or ALSA backend; Windows/macOS support planned)

**Installation**  

1. Download vse_push_to_talk.py
2. In Blender: Edit −> Preferences −> Add-ons −> Install…
3. Enable the add-on
4. Open the Video Sequence Editor and start recording 🎤

## Demo Video
[▶ Watch demo video](https://github.com/user-attachments/assets/fa5adf01-14cc-48ab-a5b2-a055c1e3d8d6)