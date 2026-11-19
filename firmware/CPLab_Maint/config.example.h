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

// ===== Machine =====
#define MACHINE_NUM            1      // 1..4
#define VIB_PIN                53     // digital output DO
#define VIB_AO_PIN             -1     // analog output, -1 if not wired
#define VIB_ACTIVE_LOW         0      // most SW-420: HIGH while vibrating
#define TH_OFF_DEFAULT         5
#define TH_HIGH_DEFAULT        60
#define CV_ABNORMAL            0.6
#define OFF_CONFIRM_S          5
#define ABN_CONFIRM_S          3
