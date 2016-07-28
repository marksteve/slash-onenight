from itertools import izip


def key(*args):
    return ':'.join(('onenight',) + args)


def pairs_to_dict(response):
    # https://github.com/andymccurdy/redis-py/blob/master/redis/client.py#L184
    "Create a dict given a list of key/value pairs"
    it = iter(response)
    return dict(izip(it, it))
