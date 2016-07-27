import json
import logging
import random

import tornado.ioloop
from enum import Enum
from tornado.websocket import websocket_connect

import requests


class Game(object):

    roles = Enum('Role', [
        'werewolf',
        'seer',
        'robber',
        'troublemaker',
        'villager',
    ])

    GAME_STARTING = 'Starting game...'
    CHECKING_PLAYERS = 'Checking players...'
    INVALID_PLAYERS_LENGTH = 'You can only have 3-10 players in this channel ' \
                             'to start a game!'
    GAME_STARTED = 'Players! Pretend to close your eyes...'

    def __init__(self, db, bot_user_id, bot_access_token, channel_id):
        self.db = db
        self.bot_user_id = bot_user_id
        self.bot_access_token = bot_access_token
        self.channel_id = channel_id

    def api(self, path, **kwargs):
        data = kwargs.get('data', {})
        data.setdefault('token', self.bot_access_token)
        kwargs.update(data=data)
        resp = requests.post(
            'https://slack.com/api/{}'.format(path),
            **kwargs).json()
        if not resp['ok']:
            raise RuntimeError(repr(resp['error']))
        return resp

    def start(self):
        resp = self.api('rtm.start')
        conn_future = websocket_connect(
            resp['url'], on_message_callback=self.on_message)
        ioloop = tornado.ioloop.IOLoop.current()
        ioloop.add_future(conn_future, self.on_connect)

    def send(self, msg):
        evt = {
            'type': 'message',
            'channel': self.channel_id,
            'text': msg,
        }
        logging.info('Send: {}'.format(evt))
        self.conn.write_message(json.dumps(evt))

    def get_players(self):
        self.send(self.CHECKING_PLAYERS)
        channel_type = 'channel' if self.channel_id.startswith('C') \
            else 'group'
        resp = self.api(
            '{}s.info'.format(channel_type), data={'channel': self.channel_id})
        channel_info = resp[channel_type]
        players = list(filter(
            lambda m: m != self.bot_user_id, channel_info['members']))
        return players

    def get_roles(self, players):
        roles = [self.roles.werewolf] * 2 \
            + [self.roles.seer, self.roles.robber, self.roles.troublemaker] \
            + [self.roles.villager] * (len(players) - 2)
        random.shuffle(roles)
        return roles

    def on_connect(self, conn_future):
        self.conn = conn_future.result()
        self.send(self.GAME_STARTING)
        players = self.get_players()
        if not (3 <= len(players) <= 5):
            self.send(self.INVALID_PLAYERS_LENGTH)
            return
        roles = self.get_roles(players)
        center = list(range(3))
        self.player_roles = list(zip(players + center, roles))
        self.send(self.GAME_STARTED)

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

