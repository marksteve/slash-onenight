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
from utils import key


class Game(object):

    roles = Enum('Role', [
        'werewolf',
        'seer',
        'robber',
        'troublemaker',
        'villager',
    ])
    ROLES_LABEL = {
        roles.werewolf: ':wolf: Werewolf',
        roles.seer: ':crystal_ball: Seer',
        roles.robber: ':gun: Robber',
        roles.troublemaker: ':smiling_imp: Troublemaker',
        roles.villager: ':man: Villager',
    }

    GAME_STARTING = 'Starting game...'
    CHECKING_PLAYERS = 'Checking players...'
    INVALID_PLAYERS_LENGTH = 'You can only have 3-10 players in this channel ' \
                             'to start a game!'
    GAME_STARTED = 'Everyone, pretend to close your eyes.'

    CENTER_1 = ':black_joker: First card'
    CENTER_2 = ':black_joker: Second card'
    CENTER_3 = ':black_joker: Third card'

    LOOK_OWN_CARD = ':black_joker: Everyone, look at your own card.'
    LOOK_OWN_CARD_ACTION = 'Look'
    LOOK_OWN_CARD_REVEAL = 'You are a {}'

    WEREWOLF_WAKE_UP = ':wolf: Werewolves, wake up and look for other ' \
                       'werewolves.'
    WEREWOLF_ATTACHMENT = 'If you are a werewolf...'
    WEREWOLF_LOOK_FOR_OTHERS = 'Look for others'
    WEREWOLF_LONE = 'You are the lone wolf'
    WEREWOLF_LONE_LOOKED = 'You already looked at a center card'
    WEREWOLF_NOT_LONE = 'You are not the lone wolf'
    WEREWOLF_LIST = 'The other werewolves are: {}'
    WEREWOLF_LONE_ATTACHMENT = 'If you are the lone wolf, check one of the ' \
                               'center cards...'
    WEREWOLF_LOOK_AT_CENTER = 'The {} is a {}'
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
        actions = data['actions']
        callback_id = data['callback_id']
        response_url = data['response_url']

        _, evt, _ = callback_id.split(':')

        if evt == 'look_own_card':
            self.on_look_own_card(user, response_url)
        elif evt == 'werewolf_look_for_others':
            self.on_werewolf_look_for_others(user, response_url)
        elif evt == 'werewolf_look_at_center':
            self.on_werewolf_look_at_center(user, actions, response_url)
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

    def get_player_ids(self):
        return list(map(lambda p: p[0], filter(
            lambda p: type(p[0]) != int, self.player_roles)))

    def get_werewolf_ids(self):
        return list(map(lambda p: p[0], filter(
            lambda p: p[1] == self.roles.werewolf, self.player_roles)))

    def get_player_werewolf_ids(self):
        return list(filter(lambda w: type(w) != int, self.get_werewolf_ids()))

    @gen.coroutine
    def start_night(self):
        self.look_own_card_done = Event()
        self.werewolves_wake_up_done = Event()
        yield [
            self.look_own_card(),
            self.werewolves_wake_up(),
            self.seer_wake_up(),
        ]

    @gen.coroutine
    def look_own_card(self):
        self.api('chat.postMessage', data={
            'channel': self.channel_id,
            'text': self.LOOK_OWN_CARD,
            'attachments': json.dumps([
                {
                    'text': None,
                    'callback_id': key('look_own_card', self.id),
                    'actions': [
                        {
                            'name': 'look_own_card',
                            'text': self.LOOK_OWN_CARD_ACTION,
                            'type': 'button',
                        },
                    ],
                },
            ]),
        })

    def on_look_own_card(self, user, response_url):
        role = dict(self.player_roles).get(user['id'])
        text = self.LOOK_OWN_CARD_REVEAL.format(self.ROLES_LABEL[role])
        requests.post(response_url, json={
            'text': text,
            'replace_original': False,
            'response_type': 'ephemeral',
        })
        player_ids = self.get_player_ids()
        look_own_key = key('look_own_players', self.id)
        self.redis.sadd(look_own_key, user['id'])

        # DEBUGGING
        self.look_own_card_done.set()

        def check_look_own(look_own_players):
            if not look_own_players:
                return

            # Check if all player werewolves have finished
            # checking on other werewolves
            for p in player_ids:
                if p not in look_own_players:
                    break
            else:
                self.look_own_card_done.set()

        self.redis.smembers(look_own_key, callback=check_look_own)

    @gen.coroutine
    def werewolves_wake_up(self):
        yield self.look_own_card_done.wait()
        # Allow werewolves to check fellow werewolves
        look_for_others_cb_id = key(
            'werewolf_look_for_others', self.id)
        look_at_center_cb_id = key(
            'werewolf_look_at_center', self.id)
        self.api('chat.postMessage', data={
            'channel': self.channel_id,
            'text': self.WEREWOLF_WAKE_UP,
            'attachments': json.dumps([
                {
                    'text': self.WEREWOLF_ATTACHMENT,
                    'callback_id': look_for_others_cb_id,
                    'actions': [
                        {
                            'name': 'werewolf_look_for_others',
                            'text': self.WEREWOLF_LOOK_FOR_OTHERS,
                            'type': 'button',
                        },
                    ],
                },
                {
                    'text': self.WEREWOLF_LONE_ATTACHMENT,
                    'callback_id': look_at_center_cb_id,
                    'actions': [
                        {
                            'name': 'center_1',
                            'value': 0,
                            'text': self.CENTER_1,
                            'type': 'button',
                        },
                        {
                            'name': 'center_2',
                            'value': 1,
                            'text': self.CENTER_2,
                            'type': 'button',
                        },
                        {
                            'name': 'center_3',
                            'value': 2,
                            'text': self.CENTER_3,
                            'type': 'button',
                        },
                    ],
                },
            ]),
        })
        player_werewolf_ids = self.get_player_werewolf_ids()
        if len(player_werewolf_ids) == 0:
            ioloop = IOLoop.current()
            ioloop.call_later(10, lambda: self.werewolves_wake_up_done.set())

    def on_werewolf_look_for_others(self, user, response_url):
        player_werewolf_ids = self.get_player_werewolf_ids()
        awake_key = key('awake_player_werewolves', self.id)

        # Check if user is an actual werewolf
        if user['id'] in player_werewolf_ids:
            if len(player_werewolf_ids) == 1:
                requests.post(response_url, json={
                    'text': self.WEREWOLF_LONE,
                    'replace_original': False,
                    'response_type': 'ephemeral',
                })
                return
            tags = map(
                lambda w: '<@{}>'.format(w),
                filter(lambda w: w != user['id'], player_werewolf_ids))
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
            for p in player_werewolf_ids:
                if p not in awake_player_werewolves:
                    break
            else:
                self.werewolves_wake_up_done.set()

        self.redis.smembers(awake_key, callback=check_awake)

    def on_werewolf_look_at_center(self, user, actions, response_url):
        player_werewolf_ids = self.get_player_werewolf_ids()
        lone_key = key('lone_wolf_looked', self.id)

        def check_looked(looked):
            if looked:
                requests.post(response_url, json={
                    'text': self.WEREWOLF_LONE_LOOKED,
                    'replace_original': False,
                    'response_type': 'ephemeral',
                })
                return
            action = actions[0]
            if user['id'] in player_werewolf_ids:
                if len(player_werewolf_ids) != 1:
                    requests.post(response_url, json={
                        'text': self.WEREWOLF_NOT_LONE,
                        'replace_original': False,
                        'response_type': 'ephemeral',
                    })
                    return
                chosen_center = int(action['value'])
                role = dict(self.player_roles).get(chosen_center)
                card_label = [self.CENTER_1,
                              self.CENTER_2,
                              self.CENTER_3][chosen_center]
                text = self.WEREWOLF_LOOK_AT_CENTER.format(
                    card_label, self.ROLES_LABEL[role])
                self.redis.set(lone_key, chosen_center)
                self.werewolves_wake_up_done.set()
            else:
                text = self.WEREWOLF_FALSE
            requests.post(response_url, json={
                'text': text,
                'replace_original': False,
                'response_type': 'ephemeral',
            })

        self.redis.exists(lone_key, callback=check_looked)

    @gen.coroutine
    def seer_wake_up(self):
        yield self.werewolves_wake_up_done.wait()
        self.api('chat.postMessage', data={
            'channel': self.channel_id,
            'text': self.SEER_WAKE_UP,
        })

