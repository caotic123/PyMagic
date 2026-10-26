# magics for the standard library models from what the standard library itself does, so unlike the models these
# use it: a clock, fixed or the real one, randomness from a seeded generator, and the Unicode database's answers

import time
import random
import unicodedata
import encodings.idna
from types import SimpleNamespace

# the originals, kept from the models
real = SimpleNamespace(
    ctime=time.ctime, time=time.time, nameprep=encodings.idna.nameprep, normalize=unicodedata.normalize,
)


def outcome_of(function, *args):
    # what a function returns, or the exception it raises, as a magic value
    try:
        return function(*args)
    except Exception as error:
        return error

def stdlib_magics(now=None, seed=0):
    # the clock reads now, in seconds since the epoch (the real clock when it's None), and randomness comes from
    # a generator seeded with seed
    chance = random.Random(seed)

    def clock():
        return int(real.time()) if now is None else now

    def ctime(seconds=None):
        yield real.ctime(clock() if seconds is None else seconds)

    def gmtime(seconds=None):
        yield clock()

    def randrange(start, stop=None, step=1):
        yield chance.randrange(start, stop, step)

    def nameprep(label):
        yield outcome_of(real.nameprep, label)

    def normalize(form, unistr):
        yield real.normalize(form, unistr)

    return {
        time: {time.ctime: ctime, time.gmtime: gmtime},
        random: {random.randrange: randrange},
        encodings.idna: {encodings.idna.nameprep: nameprep},
        unicodedata: {unicodedata.normalize: normalize},
    }
