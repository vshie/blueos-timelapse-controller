# Camera Tilt Control Using the BlueOS Camera Gimbal Setup Approach

This note explains how BlueOS drives the camera tilt servo from the Vehicle Setup -> Configure -> Camera Gimbal page, and how to reproduce the same approach from a Python script.

The relevant BlueOS file is:

```text
BlueOS/core/frontend/src/components/vehiclesetup/configuration/camera.vue
```

## What BlueOS Configures

The page is designed for setup and calibration, not normal pilot control. It configures ArduPilot mount/servo parameters, then sends MAVLink `COMMAND_LONG` messages that drive the mount to its limits while the user tunes PWM endpoints.

The important parameters are:

```text
MNT1_TYPE          Mount/gimbal type
MNT1_PITCH_MIN     Minimum reported pitch angle
MNT1_PITCH_MAX     Maximum reported pitch angle
SERVOx_FUNCTION    Servo output function; 7 means mount tilt
SERVOx_MIN         Servo PWM at one physical limit
SERVOx_MAX         Servo PWM at the other physical limit
SERVOx_REVERSED    Optional servo direction reversal
```

BlueOS finds the pitch servo by looking for:

```text
SERVOx_FUNCTION = 7
```

where `7` is ArduPilot's `MOUNT_TILT` servo function.

## MAVLink Messages BlueOS Uses

When the user edits the max PWM field, BlueOS sends:

```text
COMMAND_LONG
command = MAV_CMD_DO_GIMBAL_MANAGER_TILTPAN
param1 = 180.0
```

Then it sends:

```text
COMMAND_LONG
command = MAV_CMD_DO_MOUNT_CONTROL
param1 = 0
param2 = 0
param3 = 0
param4 = 0
param5 = 0
param6 = 0
param7 = 3
```

BlueOS comments `param7 = 3` as:

```text
MAV_MOUNT_MODE_RC_TARGETING
```

When the user edits the min PWM field, the same sequence is used, except:

```text
MAV_CMD_DO_GIMBAL_MANAGER_TILTPAN param1 = -180.0
```

The `+180` and `-180` values are intentionally beyond the normal camera range. They force the mount to drive to the configured endpoint, which helps the user tune `SERVOx_MIN` and `SERVOx_MAX`.

## Controlling Arbitrary Tilt Angles

For a Python script, the same practical control approach is:

1. Configure the autopilot so the pitch servo has `SERVOx_FUNCTION = 7`.
2. Configure the mount range using `MNT1_PITCH_MIN`, `MNT1_PITCH_MAX`, `SERVOx_MIN`, and `SERVOx_MAX`.
3. Send `MAV_CMD_DO_GIMBAL_MANAGER_TILTPAN` with the desired pitch angle in degrees.
4. Clamp requested angles to the calibrated range, for example `-90` to `+90`.

Conceptually:

```python
def set_camera_tilt(master, angle_deg):
    angle_deg = max(-90.0, min(90.0, angle_deg))

    master.mav.command_long_send(
        master.target_system,
        master.target_component,
        mavutil.mavlink.MAV_CMD_DO_GIMBAL_MANAGER_TILTPAN,
        0,          # confirmation
        angle_deg,  # param1: pitch angle in degrees
        0,          # param2: yaw angle in degrees
        0,          # param3: pitch rate, 0 means default/unspecified
        0,          # param4: yaw rate, 0 means default/unspecified
        0,          # param5: flags
        0,          # param6: reserved
        0,          # param7: gimbal device id, 0 for primary/default
    )
```

This mirrors the first command BlueOS sends, but uses the actual desired angle instead of `+180` or `-180`.

## Centering The Camera

If the mount is calibrated symmetrically, the normal way to center the camera is to command zero degrees:

```python
set_camera_tilt(master, 0.0)
```

That should result in approximately center PWM, usually `1500`, when:

```text
SERVOx_MIN = 1100
SERVOx_MAX = 1900
MNT1_PITCH_MIN = -90
MNT1_PITCH_MAX = 90
SERVOx_REVERSED is set correctly
```

If you specifically need to command a raw servo PWM of `1500`, that is a different control method from the BlueOS camera setup page. The setup page does not directly send "servo PWM = 1500" commands. It sends mount/gimbal angle commands and lets ArduPilot convert the requested mount angle into servo output.

For the BlueOS-style mount path, prefer:

```text
angle 0 deg -> ArduPilot mount controller -> SERVOx output near 1500 us
```

rather than:

```text
raw PWM 1500 us -> servo output
```

## Reading Back The Actual Output

BlueOS requests `SERVO_OUTPUT_RAW` at 2 Hz and uses that to display the current servo PWM.

For a Python script, listen for `SERVO_OUTPUT_RAW` and read the channel that corresponds to the `SERVOx_FUNCTION = 7` output:

```python
msg = master.recv_match(type="SERVO_OUTPUT_RAW", blocking=True, timeout=1)
if msg is not None:
    pwm = getattr(msg, "servo1_raw")  # replace 1 with the actual servo output number
```

If the mount tilt servo is on `SERVO9_FUNCTION`, read `servo9_raw`; if it is on `SERVO10_FUNCTION`, read `servo10_raw`, and so on.

## Angle To PWM Relationship

After calibration, ArduPilot handles angle-to-PWM conversion internally. The approximate relationship is:

```text
pwm_per_degree = (SERVOx_MAX - SERVOx_MIN) / (MNT1_PITCH_MAX - MNT1_PITCH_MIN)
```

For example:

```text
SERVOx_MIN = 1100
SERVOx_MAX = 1900
MNT1_PITCH_MIN = -90
MNT1_PITCH_MAX = 90

pwm_per_degree = 800 / 180 = 4.44 us/degree
```

With that setup:

```text
-90 deg -> about 1100 us
  0 deg -> about 1500 us
+90 deg -> about 1900 us
```

The sign may invert depending on `SERVOx_REVERSED` and the physical servo linkage.

## Minimal Python Skeleton

```python
from pymavlink import mavutil


def connect(connection_string):
    master = mavutil.mavlink_connection(connection_string)
    master.wait_heartbeat()
    return master


def set_camera_tilt(master, angle_deg):
    angle_deg = max(-90.0, min(90.0, angle_deg))

    master.mav.command_long_send(
        master.target_system,
        master.target_component,
        mavutil.mavlink.MAV_CMD_DO_GIMBAL_MANAGER_TILTPAN,
        0,
        angle_deg,
        0,
        0,
        0,
        0,
        0,
        0,
    )


def center_camera(master):
    set_camera_tilt(master, 0.0)


if __name__ == "__main__":
    master = connect("udp:127.0.0.1:14550")

    set_camera_tilt(master, -45)
    set_camera_tilt(master, 0)
    set_camera_tilt(master, 45)
    center_camera(master)
```

## Practical Checks

Before relying on angle control, verify:

```text
MNT1_TYPE is set to a servo/gimbal mode supported by the firmware.
Exactly one SERVOx_FUNCTION is set to 7 for mount tilt.
SERVOx_MIN and SERVOx_MAX match safe physical limits.
MNT1_PITCH_MIN and MNT1_PITCH_MAX match the measured camera angles.
SERVOx_REVERSED gives the expected direction.
SERVO_OUTPUT_RAW changes when commands are sent.
```

If commanding `+90` moves the camera down instead of up, fix `SERVOx_REVERSED` or swap the pitch min/max calibration as appropriate.