import json
import logging

import tornado.ioloop
from tornado.websocket import websocket_connect

import requests


class Game(object):

    def __init__(self, db, channel_id, token):
        self.db = db
        self.channel_id = channel_id
        self.token = token

    def start(self):
        resp = requests.post(
            'https://slack.com/api/rtm.start',
            data={'token': self.token}).json()
        if not resp['ok']:
            raise RuntimeError('Failed to start RTM')
        ioloop = tornado.ioloop.IOLoop.current()
        conn_future = websocket_connect(
            resp['url'], on_message_callback=self.on_message)
        ioloop.add_future(conn_future, self.on_connect)

    def on_connect(self, conn_future):
        self.conn = conn_future.result()
        self.send('Game started!')

    def on_message(self, msg):
        evt = json.loads(msg)
        error = evt.get('error', None)
        if error:
            logging.warning('Error: {}'.format(evt['error']))
            return
        evt_type = evt.get('type', None)
        handler = getattr(self, 'handle_{}'.format(evt_type), None)
        if handler:
            handler(evt)
        else:
            logging.warning('Unhandled event: {}'.format(evt))

    def send(self, msg):
        evt = {
            'type': 'message',
            'channel': self.channel_id,
            'text': msg,
        }
        logging.info('Send: {}'.format(evt))
        self.conn.write_message(json.dumps(evt))

