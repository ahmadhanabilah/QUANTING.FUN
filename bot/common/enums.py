from enum import Enum, auto


class Venue(Enum):
    L = auto()   # Lighter
    E = auto()   # Extended


class Side(Enum):
    LONG = auto()
    SHORT = auto()


class ActionType(Enum):
    NONE = auto()
    MAKE = auto()    # place limit order
    TAKE = auto()    # send market order
    CANCEL = auto()  # cancel maker order
