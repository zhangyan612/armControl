# import can
# import struct

# # Create a CAN bus object
# bus = can.interface.Bus("COM12", bustype="socketcan")

# # Node ID of the ODrive you want to communicate with
# node_id = 1

# # Put axis into closed loop control state
# bus.send(can.Message(
#     arbitration_id=(node_id << 5 | 0x07),  # 0x07: Set_Axis_State
#     data=struct.pack('<I', 8),  # 8: AxisState.CLOSED_LOOP_CONTROL
#     is_extended_id=False
# ))

# # Wait for axis to enter closed loop control
# for msg in bus:
#     if msg.arbitration_id == (node_id << 5 | 0x01):  # 0x01: Heartbeat
#         error, state, result, traj_done = struct.unpack('<IBBB', bytes(msg.data[:7]))
#         if state == 8:  # 8: AxisState.CLOSED_LOOP_CONTROL
#             break

# # Set velocity to 1.0 turns/s
# bus.send(can.Message(
#     arbitration_id=(node_id << 5 | 0x0d),  # 0x0d: Set_Input_Vel
#     data=struct.pack('<f', 1.0), 
#     is_extended_id=False
# ))

# # Print encoder feedback
# for msg in bus:
#     if msg.arbitration_id == (node_id << 5 | 0x0b):  # 0x0b: Get_Encoder_Estimates
#         pos_estimate, vel_estimate = struct.unpack('<ff', bytes(msg.data[:8]))
#         print("Position:", pos_estimate, "Velocity:", vel_estimate)






# import can

# bus = can.interface.Bus("can0", bustype="socketcan")

# # Flush CAN RX buffer so there are no more old pending messages
# while not (bus.recv(timeout=0) is None): pass

# node_id = 0 # must match `<odrv>.axis0.config.can.node_id`. The default is 0.
# cmd_id = 0x01 # heartbeat command ID
# message_id = (node_id << 5 | cmd_id)

# import struct

# for msg in bus:
#   if msg.arbitration_id == message_id:
#       error, state, result, traj_done = struct.unpack('<IBBB', bytes(msg.data[:7]))
#       break
# print(error, state, result, traj_done)





import can


def send_one():
    """Sends a single message."""

    # this uses the default configuration (for example from the config file)
    # see https://python-can.readthedocs.io/en/stable/configuration.html
    # with can.Bus() as bus:
        # Using specific buses works similar:
    # bus = can.Bus(interface='socketcan', channel='vcan0', bitrate=5000000)
    bus = can.Bus("can0", bustype="socketcan", bitrate=5000000)

    # bus = can.Bus(interface='ixxat', channel=0, bitrate=250000)
    # bus = can.Bus(interface='vector', app_name='CANalyzer', channel=0, bitrate=250000)
    # ...

    msg = can.Message(
        arbitration_id=0xC0FFEE, data=[0, 25, 0, 1, 3, 1, 4, 1], is_extended_id=True
    )

    try:
        bus.send(msg)
        print(f"Message sent on {bus.channel_info}")
    except can.CanError:
        print("Message NOT sent")


if __name__ == "__main__":
    send_one()