// Copy this file to config.h (same folder) and fill in your values.
// config.h is ignored by Git. Values below are placeholders / suggested defaults:
// check them against the config.h you flashed on the lab Tab5.
#pragma once

// ===== Network (secret: never commit config.h) =====
#define WIFI_SSID    "your-wifi"
#define WIFI_PASS    "your-password"
#define BROKER_HOST  "192.168.0.10"   // laptop running Mosquitto
#define BROKER_PORT  1883
#define MQTT_USER    ""               // empty = anonymous
#define MQTT_PASS    ""
#define TOPIC_ROOT   "cplab"

// ===== Station =====
#define STATION_NUM            1      // 1, 2 or 3
#define HALL_PIN               53     // G53 = Grove Port A
#define HALL_ACTIVE_LOW        1
#define MIN_PIECE_INTERVAL_MS  800    // debounce: one magnet passage = one piece
#define TAKT_DEFAULT_S         20
#define WIP_MAX_DEFAULT        3
