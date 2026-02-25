# dji_srt_tool

A command-line tool for converting DJI drone telemetry SRT files into clean, readable subtitle files for use in YouTube videos and other video editors.

DJI drones record telemetry (altitude, GPS, ISO, shutter speed, etc.) as subtitle data at the video frame rate — typically 30 entries per second. That's far too dense to use directly as subtitles. This tool lets you pick exactly which fields to show, set a minimum update interval, and outputs a clean SRT file that only updates when the data actually changes.

Tested with the **DJI Air 3S**. Should work with other modern DJI drones that produce the same SRT format.

## Features

- **Interactive TUI** — terminal UI for selecting and configuring fields before export
- **Change-driven output** — subtitles only update when the displayed data actually changes, after a configurable minimum interval
- **Field discovery** — automatically reads all telemetry fields present in your file with sample values
- **Custom labels** — optionally prefix any field with a label (e.g. `Alt: 52m`)
- **Reorderable fields** — drag fields up and down to control output order
- **Metric/imperial toggle** — switch between `m/m/s` and `ft/mph` with live preview in the TUI
- **No dependencies** — pure Python standard library, no pip install required

## Requirements

- Python 3.11+
- macOS or Linux (the `curses` module is not available on Windows; use WSL2)

## Usage

```bash
# Launch the interactive TUI
python dji_srt_tool.py input.SRT

# Specify an output path (default: input_clean.srt)
python dji_srt_tool.py input.SRT output.srt

# Print all available telemetry fields and exit
python dji_srt_tool.py input.SRT --scan

# Set a different starting minimum interval (milliseconds)
python dji_srt_tool.py input.SRT --interval 3000
```

## TUI Controls

| Key | Action |
|-----|--------|
| `↑` / `↓` | Navigate fields |
| `Space` | Toggle field on/off |
| `[` / `]` | Move field up/down in output order |
| `a` | Select all / deselect all |
| `l` | Set a custom label for the field (auto-selects it) |
| `u` | Toggle between metric and imperial units |
| `+` / `-` | Adjust minimum update interval (±500ms) |
| `Enter` | Process and save the output SRT |
| `q` | Quit without saving |

Enabled fields float to the top of the list automatically. A dotted divider separates enabled fields from disabled ones.

## How the output works

A new subtitle block is written only when **both** conditions are met:

1. The displayed content has changed from the last emitted block
2. At least the minimum interval has elapsed since the last update

Each block's end time is extended to meet the next change, so subtitles display continuously with no gaps.

## Supported fields (Air 3S)

| Field | Description |
|-------|-------------|
| `iso` | ISO sensitivity |
| `shutter` | Shutter speed |
| `fnum` | Aperture (f-number × 100, e.g. `280` = f/2.8) |
| `ev` | Exposure compensation |
| `ct` | Color temperature (Kelvin) |
| `color_md` | Color mode / picture profile |
| `focal_len` | Focal length (mm) |
| `latitude` | GPS latitude |
| `longitude` | GPS longitude |
| `rel_alt` | Altitude relative to takeoff point |
| `abs_alt` | Absolute altitude (above sea level) |

Speed and distance fields (`speed`, `h_speed`, `v_speed`, `distance`) may be present depending on firmware version. Use `--scan` to see exactly what's in your file.

## Unit conversions

Fields with unit conversions applied automatically when imperial mode is active:

| Field | Metric | Imperial |
|-------|--------|----------|
| `rel_alt`, `abs_alt`, `altitude`, `height`, `distance` | m | ft |
| `speed`, `h_speed`, `v_speed` | m/s | mph |

To add conversions for additional fields, edit the `UNIT_CONVERSIONS` dictionary near the top of the script.

## License

MIT
