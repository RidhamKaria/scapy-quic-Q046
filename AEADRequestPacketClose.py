from scapy.fields import *
from scapy.packet import Packet

from XStrFixedLenField import XStrFixedLenField
from util.string_to_ascii import string_to_ascii


class AEADRequestPacketClose(Packet):
    """
    Class that holds the raw data for the AEAD Packets
    But without the div nonce, used for sending the requests.
    """
    name = "AEAD Packet"

    fields_desc = [
        XByteField("Public_Flags", 0x0c),
        StrFixedLenField("CID", string_to_ascii(""), 8),
        StrFixedLenField("Packet_Number", 1, 1), # LEShortField
        # XStrFixedLenField("Message_Authentication_Hash", string_to_ascii(""), 12),
    ]
