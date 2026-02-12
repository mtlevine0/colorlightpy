# colorlight-driver

Python driver for streaming RGB frames to a **Colorlight 5A-75B** LED receiver card over raw Ethernet (Layer 2). The protocol was reverse-engineered by the open-source community ([chubby75](https://github.com/q3k/chubby75), [LED_Matrix-1](https://github.com/kostaman/LED_Matrix-1)). Includes built-in test pattern generators for display validation.

## Requirements

- Python 3.9+
- [Npcap](https://npcap.com) (Windows only)
- Administrator / root privileges for raw socket access

## Setup

```bash
pip install -r requirements.txt
```

## Usage

```bash
# List available network interfaces
python main.py --list-interfaces

# Stream a scrolling rainbow at 30 fps
python main.py -i Ethernet -W 192 -H 384 --fps 30 -p rainbow

# Static colour bars at full brightness
python main.py -i Ethernet -p bars -b 255
```

## License

[MIT](LICENSE)
