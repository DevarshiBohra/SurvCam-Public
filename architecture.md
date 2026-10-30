# SurvCam architecture

This diagram shows the normal traffic paths for a self-hosted installation. Replace the example hostname and LAN address with the values configured for your own server.

```mermaid
flowchart LR
    OWNER[User]
    APP[SurvCam Android app]

    subgraph EDGE[Public internet]
        DNS[Cloudflare DNS<br/>your public hostname]
        TUNNEL[Cloudflare Tunnel<br/>HTTPS ingress]
        TELEGRAM[Telegram Bot API<br/>login codes]
        FCM[Firebase Cloud Messaging<br/>optional push notifications]
    end

    subgraph LAN[Home LAN]
        subgraph SERVER[Ubuntu server]
            CONNECTOR[cloudflared connector<br/>outbound tunnel connection]
            API[SurvCam FastAPI backend<br/>REST API · auth · live view · playback · OTA]
            MQTT[Mosquitto broker<br/>TCP 1883 · LAN only]
            DB[(SQLite databases<br/>users · cameras · telemetry · events)]
            MEDIA[(Recording and snapshot storage)]
        end

        subgraph CAMERAS[SurvCam cameras]
            WIFI[ESP32-CAM<br/>Wi-Fi firmware]
            ETH[ESP32-CAM + W5500<br/>Ethernet firmware]
            BRIDGE[ESP32 bridge + camera node<br/>optional two-board setup]
        end
    end

    OWNER --> APP
    APP -->|HTTPS API and video playback| DNS
    DNS --> TUNNEL
    TUNNEL --> CONNECTOR
    CONNECTOR -->|HTTP localhost:8000| API
    APP -.->|Optional direct HTTP on home Wi-Fi| API

    WIFI -->|MQTT heartbeats and telemetry| MQTT
    ETH -->|MQTT heartbeats and telemetry| MQTT
    BRIDGE -->|MQTT heartbeats and telemetry| MQTT
    API <-->|Heartbeat subscription and camera commands| MQTT

    API -->|Pull MJPEG stream over LAN, TCP 81| WIFI
    API -->|Pull MJPEG stream over LAN, TCP 81| ETH
    API -->|Pull camera stream over LAN| BRIDGE
    API -->|Store recordings and snapshots| MEDIA
    API -->|Store application data| DB

    API -->|2FA login messages| TELEGRAM
    API -->|Optional push messages| FCM
    FCM -.->|Notifications| APP
    WIFI -.->|Firmware download over LAN| API
    ETH -.->|Firmware download over LAN| API
    BRIDGE -.->|Firmware download over LAN| API

    classDef client fill:#e8f1ff,stroke:#3973ac,color:#142b43
    classDef cloud fill:#fff1dc,stroke:#c47b18,color:#52320b
    classDef server fill:#e8f5e9,stroke:#43844a,color:#183d1d
    classDef device fill:#f3e8ff,stroke:#8052a3,color:#38204b
    class APP,OWNER client
    class DNS,TUNNEL,TELEGRAM,FCM cloud
    class CONNECTOR,API,MQTT,DB,MEDIA server
    class WIFI,ETH,BRIDGE device
```

## Network boundaries

- The Android app reaches the backend over HTTPS through Cloudflare when away from home. On home Wi-Fi it can use the server's LAN address directly.
- `cloudflared` makes an outbound connection to Cloudflare and forwards the public hostname to the local backend. A router port-forward is not needed.
- Cameras use the server's LAN address for MQTT, video, and firmware downloads. Cloudflare Tunnel is not the MQTT path.
- Keep MQTT port `1883` on the home LAN. Do not forward it from the router or publish it as a Cloudflare public hostname.
- Telegram 2FA is required by the backend. Firebase push notifications are optional and require each installer to provide their own Firebase configuration.
