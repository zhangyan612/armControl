import websocket
import json
import armPortConnect
import pika
import time
remoteHost = '192.168.0.247'  #'localhost'
credential = pika.credentials.PlainCredentials('yan', 'yan', erase_on_connect=False)

connection = pika.BlockingConnection(
    pika.ConnectionParameters(host=remoteHost, credentials=credential))
channel = connection.channel()

channel.exchange_declare(exchange='arm', exchange_type='fanout')

# when receiving message from server, send it to MQ
previousState = []

def list_different(list1, list2):
    for item1, item2 in zip(list1, list2):
        if abs(item1 - item2) > 1:
            return True
    return False


def on_message(ws, message):
    global previousState  # declare previousState as global
    global list_different

    msg = json.loads(message)
    connected = msg['payload']['connected']
    if not connected:
        # http call
        armPortConnect.connect_robot()
    if msg['event'] == 'StatusUpdate':
        angle = msg['payload']['jointState']['jointAngle']
        print(str(angle))
        channel.basic_publish(exchange='arm', routing_key='', body=message)

        # if list_different(angle, previousState):
        #     #send this to MQ
        #     print(msg['payload']['jointState'])
        #     channel.basic_publish(exchange='arm', routing_key='', body=message)
        #     time.sleep(0.5)

        # previousState = angle

    # print(msg['payload']['jointState'])
    if msg['event'] == 'TaskUpdate' and msg['payload']['task'] != None:
        print("Task运行进度:", msg['payload']['progress'] * 100, " %")
    if msg['event'] == 'TaskUpdate' and msg['payload']['task'] == None:
        print("Task运行完成")

def continuousConnection():
    # define the WebSocket URL
    ws_url = "ws://localhost:8080/api/ws"
    # start the WebSocket connection
    ws = websocket.WebSocketApp(ws_url, on_message=on_message) #, on_open=on_open
    ws.run_forever()

if __name__ == '__main__':
    continuousConnection()