# BACnet-MQTT Gateway

An industrial-grade IoT gateway that bridges BACnet (IP and MS/TP) and Modbus RTU devices to an MQTT broker.
Provides a modern React frontend for real-time monitoring, point mapping, chart visualization, and system diagnostics.

## Features

- **BACnet/IP Gateway**: Uses BACpypes3 / BAC0 for robust, high-performance polling. Includes optimized COV (Change-of-Value) reporting.
- **BACnet MS/TP Master Stack**: Includes a custom, pure-Python MS/TP Master node implementation (ASHRAE 135-2016 §9.3) capable of joining token rings, sending WhoIs, and parsing I-Am and ReadProperty responses.
- **MS/TP to MQTT Bridge**: Directly bridges legacy MS/TP serial devices (RS485) to MQTT via the custom Python stack.
- **Modbus RTU Scanner**: Includes an automated scanning tool `modbus_scanner.py` for health analysis of Modbus serial buses.
- **Real-time Frontend**: A responsive, React-based dashboard (Vite + Material UI) serving live charts, device discovery tools, and an interactive point mapping interface.
- **Persistent Storage**: Uses SQLite for historic data storage and dynamic configuration reloads without service disruption.
- **Dockerized Deployment**: Clean deployment model with decoupled frontend, backend, and MQTT broker containers.

## Architecture

```text
┌─────────────────┐       ┌─────────────────┐       ┌─────────────────┐
│                 │       │                 │       │                 │
│  BACnet/IP      │     ┌─▶  Gateway Engine │───────▶ Mosquitto       │
│  (Chillers,     │────┐│ │  (FastAPI +     │       │ (MQTT Broker)   │
│   Energy Meters)│    ││ │   BAC0)         │       │                 │
│                 │    ││ │                 │       │                 │
├─────────────────┤    ││ └─────────────────┘       └─────────────────┘
│                 │    ││                                   ▲
│  MS/TP RS485    │    ││ ┌─────────────────┐               │
│  (Thermostats,  │────┼─▶│ MS/TP Bridge    │───────────────┘
│   FCUs)         │    │  │ (Raw Python)    │
│                 │    │  └─────────────────┘
└─────────────────┘    │
                       │  ┌─────────────────┐
                       └─▶│  React Frontend │
                          │  (Dashboard)    │
                          └─────────────────┘
```

## Tools Included

The `tools/` directory contains specialized diagnostic scripts that bypass the main gateway to test raw protocols:

### MS/TP Tools (`tools/mstp/`)
- `mstp_master.py`: Complete Token Ring state machine. Send `WhoIs` and `ReadProperty` to active bus nodes.
- `mstp_mqtt_bridge.py`: Continuous polling script for mapping MS/TP points directly to Mosquitto MQTT.
- `mstp_sniffer.py`: Passive bus health analyzer (detects baudrate, CRC errors, frame timing).

### Modbus Tools (`tools/modbus/`)
- `modbus_scanner.py`: Scans an RTU bus (e.g. 1-247) to find active slave IDs and valid holding/input registers.

## Deployment

### Docker Compose (Production)
```bash
docker-compose up -d --build
```
This stands up the FastAPI backend, Nginx frontend, and the SQLite persistence layer.

### Bare Metal (Raspberry Pi/Debian)
```bash
# Setup Python VENV
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# Run Gateway Engine
python backend/gateway_engine.py

# (Optional) Run MS/TP Bridge in parallel
python tools/mstp/mstp_mqtt_bridge.py --config config/runtime_config.json
```
