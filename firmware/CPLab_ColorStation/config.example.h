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

// ===== Station 4 (colour) =====
#define HALL_PIN               53
#define HALL_ACTIVE_LOW        1
#define MIN_PIECE_INTERVAL_MS  800
#define TAKT_DEFAULT_S         20
#define CAPTURE_DELAY_MS       300    // Hall trigger -> frame capture
#define ROI_PERCENT            30     // central square analysed, % of frame height
#define UNKNOWN_DIST           60     // RGB distance above which colour = UNKNOWN
// ===== Camera (see tab5_camera.h, experimental) =====
#define USE_CAMERA             1
#define CAM_I2C_PORT           0
#define CAM_SDA                31
#define CAM_SCL                32
