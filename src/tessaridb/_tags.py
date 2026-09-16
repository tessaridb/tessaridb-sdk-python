"""The tag bytes of §4. Permanent: never reused for a different type and never
renumbered, because data already written carries them.
"""

TAG_NONE = 0x01
TAG_NULL = 0x02
TAG_BOOL = 0x03
TAG_NUMBER = 0x04
TAG_STRING = 0x05
TAG_BYTES = 0x06
TAG_DURATION = 0x07
TAG_DATETIME = 0x08
TAG_UUID = 0x09
TAG_TABLE = 0x0A
TAG_RECORD = 0x0B
TAG_ARRAY = 0x0C
TAG_OBJECT = 0x0D
TAG_RANGE = 0x0E
TAG_SET = 0x0F
TAG_GEOMETRY = 0x10
TAG_REGEX = 0x11

# Three numeric encodings, two conventions: the integer goes through the
# inverting i64, while the float bits and the decimal mantissa are written plain.
NUMBER_INTEGER = 0x01
NUMBER_FLOAT = 0x02
NUMBER_DECIMAL = 0x03

BOUND_UNBOUNDED = 0x01
BOUND_INCLUDED = 0x02
BOUND_EXCLUDED = 0x03

ID_INTEGER = 0x01
ID_TEXT = 0x02
ID_UUID = 0x03
ID_BYTES = 0x04

SHAPE_POINT = 0x01
SHAPE_LINE = 0x02
SHAPE_POLYGON = 0x03
SHAPE_MULTIPOINT = 0x04
SHAPE_MULTILINE = 0x05
SHAPE_MULTIPOLYGON = 0x06
SHAPE_COLLECTION = 0x07

UUID_WIDTH = 16
