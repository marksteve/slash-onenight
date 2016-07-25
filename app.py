import os

import tornado.escape
import tornado.ioloop
import tornado.web
from tornado.options import define, options, parse_command_line

import redis
import requests


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
    pass


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
    tornado.ioloop.IOLoop.current().start()


if __name__ == '__main__':
    main()

