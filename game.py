import json
import logging
import random
from uuid import uuid4

from enum import Enum
from tornado import gen
from tornado.ioloop import IOLoop
from tornado.locks import Event
from tornado.websocket import websocket_connect

import requests
import toredis


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
    GAME_STARTED = 'Everyone, pretend to close your eyes.'

    ATTACHMENT_TEXT = 'If you are one...'

    WEREWOLF_WAKE_UP = ':wolf: Werewolves, wake up and look for other ' \
                       'werewolves.'
    WEREWOLF_ACTION = 'Open your eyes'
    WEREWOLF_LIST = 'The other werewolves are: {}'
    WEREWOLF_FALSE = 'You are not a werewolf!'

    SEER_WAKE_UP = ':crystal_ball: Seer, wake up. You make look at another ' \
                   'player\'s card or two of the center cards.'

    def __init__(self, bot_user_id, bot_access_token, channel_id, options):
        self.id = str(uuid4())
        self.bot_user_id = bot_user_id
        self.bot_access_token = bot_access_token
        self.channel_id = channel_id

        self.redis = toredis.Client()
        self.redis.connect(host=options.redis_host)

        self.pubsub = toredis.Client()
        self.pubsub.connect(host=options.redis_host)
        self.pubsub.subscribe(self.id, callback=self.on_button)

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
        ioloop = IOLoop.current()
        ioloop.add_future(conn_future, self.on_connect)

    def send(self, msg):
        evt = {
            'type': 'message',
            'channel': self.channel_id,
            'text': msg,
        }
        logging.info('Send: {}'.format(evt))
        self.conn.write_message(json.dumps(evt))

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
        logging.info(repr(self.player_roles))
        ioloop = IOLoop.current()
        ioloop.add_callback(self.start_night)
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
            logging.debug('Unhandled event: {}'.format(evt))

    def on_button(self, resp):
        resp_type, callback_id, payload = resp
        if resp_type != 'message':
            return

        data = json.loads(payload)
        user = data['user']
        callback_id = data['callback_id']
        response_url = data['response_url']

        _, evt, _ = callback_id.split(':')

        if evt == 'werewolves_wake_up':
            self.on_werewolf_button(user, response_url)
        else:
            logging.warning('Unhandled button: {}', evt)

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

    def get_werewolves(self):
        return list(map(lambda p: p[0], filter(
            lambda p: p[1] == self.roles.werewolf, self.player_roles)))

    def get_player_werewolves(self):
        return list(filter(lambda w: type(w) != int, self.get_werewolves()))

    @gen.coroutine
    def start_night(self):
        self.werewolves_wake_up_done = Event()
        yield [
            self.werewolves_wake_up(),
            self.seer_wake_up(),
        ]

    @gen.coroutine
    def werewolves_wake_up(self):
        # Allow werewolves to check fellow werewolves
        callback_id = 'onenight:werewolves_wake_up:{}'.format(self.id)
        self.api('chat.postMessage', data={
            'channel': self.channel_id,
            'text': self.WEREWOLF_WAKE_UP,
            'attachments': json.dumps([
                {
                    'text': self.ATTACHMENT_TEXT,
                    'callback_id': callback_id,
                    'actions': [
                        {
                            'name': 'werewolves_wake_up',
                            'text': self.WEREWOLF_ACTION,
                            'type': 'button',
                        },
                    ],
                },

            ]),
        })
        player_werewolves = self.get_player_werewolves()
        if len(player_werewolves) == 0:
            ioloop = IOLoop.current()
            ioloop.call_later(5, lambda: self.werewolves_wake_up_done.set())

    def on_werewolf_button(self, user, response_url):
        player_werewolves = self.get_player_werewolves()
        awake_key = 'onenight:awake_player_werewolves:{}'.format(self.id)

        # Check if user is an actual werewolf
        if user['id'] in player_werewolves:
            tags = map(
                lambda w: '<@{}>'.format(w),
                filter(lambda w: w != user['id'], player_werewolves))
            text = self.WEREWOLF_LIST.format(', '.join(tags))
            self.redis.sadd(awake_key, user['id'])
        else:
            text = self.WEREWOLF_FALSE
        requests.post(response_url, json={
            'text': text,
            'replace_original': False,
            'response_type': 'ephemeral',
        })

        def check_awake(awake_player_werewolves):
            if not awake_player_werewolves:
                return
            # Check if all player werewolves have finished
            # checking on other werewolves
            for p in player_werewolves:
                if p not in awake_player_werewolves:
                    break
            else:
                self.werewolves_wake_up_done.set()

        self.redis.smembers(awake_key, callback=check_awake)

    @gen.coroutine
    def seer_wake_up(self):
        yield self.werewolves_wake_up_done.wait()
        # callback_id = 'onenight:seer_wake_up:{}'.format(self.id)
        self.api('chat.postMessage', data={
            'channel': self.channel_id,
            'text': self.SEER_WAKE_UP,
        })

