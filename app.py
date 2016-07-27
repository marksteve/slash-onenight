import json
import os

import tornado.ioloop
import tornado.web
from tornado.options import define, options, parse_command_line

import requests
import toredis
from game import Game
from utils import pairs_to_dict


define('port', default=8000, help='run on the given port', type=int)
define('debug', default=False, help='run in debug mode')
define('redis_host', default='localhost', help='redis host')

client_id = os.environ['SLACK_CLIENT_ID']
client_secret = os.environ['SLACK_CLIENT_SECRET']

redis = toredis.Client()


class MainHandler(tornado.web.RequestHandler):

    def get(self):
        self.render('index.html', client_id=client_id, has_access=False)


class OAuthHandler(tornado.web.RequestHandler):

    def get(self):
        code = self.get_query_argument('code')
        resp = requests.post('https://slack.com/api/oauth.access', data={
            'client_id': client_id,
            'client_secret': client_secret,
            'code': code,
        }).json()
        redis.hmset('onenight:bot:{}'.format(resp['team_id']), resp['bot'])
        self.render('index.html', has_access=True)


class CommandHandler(tornado.web.RequestHandler):

    def post(self):
        command = self.get_body_argument('command')
        if command != '/onenight':
            return

        team_id = self.get_body_argument('team_id')
        redis.hgetall(
            'onenight:bot:{}'.format(team_id), callback=self.start_game)

        self.write('Summoning a GM...')

    def start_game(self, bot):
        bot = pairs_to_dict(bot)
        bot_user_id = bot['bot_user_id']
        bot_access_token = bot['bot_access_token']
        channel_id = self.get_body_argument('channel_id')
        Game(
            bot_user_id,
            bot_access_token, channel_id, options).start()


class ButtonHandler(tornado.web.RequestHandler):

    def post(self):
        payload = self.get_body_argument('payload')
        data = json.loads(payload)
        callback_id = data['callback_id']
        _, evt, game_id = callback_id.split(':')
        redis.publish(callback_id, payload)


def main():
    parse_command_line()
    redis.connect(host=options.redis_host)
    app = tornado.web.Application(
        [
            (r'/', MainHandler),
            (r'/oauth', OAuthHandler),
            (r'/command', CommandHandler),
            (r'/button', ButtonHandler),
        ],
        template_path=os.path.join(os.path.dirname(__file__), 'templates'),
        static_path=os.path.join(os.path.dirname(__file__), 'static'),
        debug=options.debug,
    )
    app.listen(options.port)
    ioloop = tornado.ioloop.IOLoop.current()
    ioloop.start()


if __name__ == '__main__':
    main()

