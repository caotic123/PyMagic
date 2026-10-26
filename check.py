import inspect
import itertools
from inspect import signature

def test(test1, *kwargs):
    return

def main():
    test.s = 's'
    print(test.s)
   # print([obj[0] for obj in inspect.getmembers(test)])

if __name__ == '__main__':
    main()