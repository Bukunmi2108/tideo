"""Single source of truth for the fixture suite.

kinds:
  ffmpeg   - one ffmpeg invocation: inputs + args + output
  speech   - flite TTS -> wav, then mux looped audio over a video source
  truncate - build a source (no faststart), keep the first `keep` fraction of bytes
"""

FIXTURES = [
    {
        "name": "short.mp4",
        "kind": "ffmpeg",
        "inputs": [
            "-f", "lavfi", "-i", "testsrc2=size=1920x1080:rate=30",
            "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000",
        ],
        "args": [
            "-t", "30", "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart",
        ],
        "expect": {"video": "h264", "width": 1920, "height": 1080, "audio": "aac", "duration": 30.0},
    },
    {
        "name": "portrait.mp4",
        "kind": "ffmpeg",
        "inputs": [
            "-f", "lavfi", "-i", "testsrc2=size=720x1280:rate=30",
            "-f", "lavfi", "-i", "sine=frequency=440",
        ],
        "args": ["-t", "30", "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac"],
        "expect": {"video": "h264", "width": 720, "height": 1280, "audio": "aac", "duration": 30.0},
    },
    {
        "name": "lowres.mp4",
        "kind": "ffmpeg",
        "inputs": [
            "-f", "lavfi", "-i", "testsrc2=size=854x480:rate=30",
            "-f", "lavfi", "-i", "sine=frequency=440",
        ],
        "args": ["-t", "30", "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac"],
        "expect": {"video": "h264", "width": 854, "height": 480, "audio": "aac", "duration": 30.0},
    },
    {
        "name": "noaudio.mp4",
        "kind": "ffmpeg",
        "inputs": ["-f", "lavfi", "-i", "testsrc2=size=1920x1080:rate=30"],
        "args": ["-t", "30", "-an", "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p"],
        "expect": {"video": "h264", "width": 1920, "height": 1080, "audio": None, "duration": 30.0},
    },
    {
        "name": "screencap.mkv",
        "kind": "ffmpeg",
        "inputs": [
            "-f", "lavfi", "-i", "testsrc2=size=2560x1440:rate=30",
            "-f", "lavfi", "-i", "anoisesrc=color=pink",
        ],
        "args": [
            "-t", "60", "-c:v", "libvpx-vp9", "-deadline", "realtime", "-cpu-used", "8",
            "-b:v", "2M", "-c:a", "libopus",
        ],
        "expect": {"video": "vp9", "width": 2560, "height": 1440, "audio": "opus", "duration": 60.0},
    },
    {
        "name": "music.mp4",
        "kind": "ffmpeg",
        "inputs": [
            "-f", "lavfi", "-i", "testsrc2=size=1280x720:rate=30",
            "-f", "lavfi", "-i",
            "sine=frequency=440[a];sine=frequency=554[b];sine=frequency=659[c];[a][b][c]amix=inputs=3",
        ],
        "args": ["-t", "30", "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac"],
        "expect": {"video": "h264", "width": 1280, "height": 720, "audio": "aac", "duration": 30.0},
    },
    {
        "name": "talking.mp4",
        "kind": "speech",
        "text": "The quick brown fox jumps over the lazy dog. Pack my box with five dozen liquor jugs. "
                "How razorback jumping frogs can level six piqued gymnasts.",
        "voice": "kal",
        "video_input": ["-f", "lavfi", "-i", "testsrc2=size=1280x720:rate=30"],
        "args": [
            "-map", "0:v", "-map", "1:a", "-t", "60",
            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac",
        ],
        "expect": {"video": "h264", "width": 1280, "height": 720, "audio": "aac", "duration": 60.0},
    },
    {
        "name": "corrupt.mp4",
        "kind": "truncate",
        "inputs": [
            "-f", "lavfi", "-i", "testsrc2=size=1280x720:rate=30",
            "-f", "lavfi", "-i", "sine=frequency=440",
        ],
        "args": ["-t", "10", "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac"],
        "keep": 0.6,
        "expect": {"unreadable": True},
    },
    {
        "name": "notavideo.mp4",
        "kind": "ffmpeg",
        "inputs": ["-f", "lavfi", "-i", "sine=frequency=440:sample_rate=44100"],
        "args": ["-t", "5", "-vn", "-c:a", "aac"],
        "expect": {"video": None, "audio": "aac", "duration": 5.0},
    },
]
