import os

import tornado.ioloop
import tornado.web
from tornado.escape import to_basestring
from tornado.options import define, options, parse_command_line

import redis
import requests
from game import Game


define("port", default=8000, help="run on the given port", type=int)
define("debug", default=False, help="run in debug mode")

client_id = os.environ['SLACK_CLIENT_ID']
client_secret = os.environ['SLACK_CLIENT_SECRET']

db = redis.StrictRedis(host=os.environ.get('REDIS_HOST', 'localhost'))


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
        db.hmset('onenight:{}:bot'.format(resp['team_id']), resp['bot'])
        self.render('index.html', has_access=True)


class CommandHandler(tornado.web.RequestHandler):

    def post(self):
        command = self.get_body_argument('command')
        if command != '/onenight':
            return

        team_id = self.get_body_argument('team_id')
        bot = db.hgetall('onenight:{}:bot'.format(team_id))
        bot_user_id = to_basestring(bot[b'bot_user_id'])
        bot_access_token = to_basestring(bot[b'bot_access_token'])
        channel_id = self.get_body_argument('channel_id')
        Game(db, bot_user_id, bot_access_token, channel_id).start()

        self.write('Summoning a GM...')


class MessagesHandler(tornado.web.RequestHandler):
    pass


def main():
    parse_command_line()
    app = tornado.web.Application(
        [
            (r'/', MainHandler),
            (r'/oauth', OAuthHandler),
            (r'/command', CommandHandler),
            (r'/messages', MessagesHandler),
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

