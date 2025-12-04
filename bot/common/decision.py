from bot.common.enums import ActionType, Venue, Side


class Decision:
    def __init__(self,
                 action_type: ActionType = ActionType.NONE,
                 venue: Venue = None,
                 side: Side = None,
                 price: float = None,
                 reason: str = None,
                 direction: str = None):
        self.action_type = action_type  # MAKE / TAKE / CANCEL / NONE
        self.venue = venue              # Venue.L or Venue.E
        self.side = side                # Side.LONG / SHORT
        self.price = price              # only required for MAKE
        self.reason = reason            # spread key or rationale
        self.direction = direction      # "entry" / "exit" / None

    def __repr__(self):
        return (f"Decision(action={self.action_type}, "
                f"venue={self.venue}, side={self.side}, price={self.price}, "
                f"reason={self.reason}, dir={self.direction})")
