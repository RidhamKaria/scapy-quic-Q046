import datetime
import struct
import time
import os
import json
import random
from collections import Counter
from statistics import median, mean
from Crypto.Cipher import AES

from peewee import OperationalError
from scapy import route  # DO NOT REMOVE!!
from scapy.config import conf
from scapy.layers.inet import IP, UDP
from scapy.all import Raw, bytes_hex
from scapy.sendrecv import send, sr
from scapy.supersocket import L3RawSocket

from ACKNotificationPacket import AckNotificationPacket
from ACKPacket import ACKPacket
from AEADPacketDynamic import AEADPacketDynamic, AEADFieldNames
from AEADRequestPacket import AEADRequestPacket
from AEADRequestPacketClose import AEADRequestPacketClose
from DynamicCHLOPacket import DynamicCHLOPacket
from FramesProcessor import FramesProcessor
from FullCHLOPacket import FullCHLOPacket
from FullCHLOPacketNoPadding import FullCHLOPacketNoPadding
from PacketNumberInstance import PacketNumberInstance
from PingPacket import PingPacket
from QUIC_46 import QUICHeader
from RejectionPacket import RejectionPacket
from SecondACKPacket import SecondACKPacket
from caching.CacheInstance import CacheInstance
from caching.SessionModel import SessionModel
from connection.ConnectionInstance import ConnectionEndpoint, CryptoConnectionManager
from connection.LearnerConnectionInstance import LearnerConnectionInstance
from crypto.CryptoManager import CryptoManager
from crypto.dhke import dhke
from crypto.fnv128a import FNV128A
from events.Events import SendInitialCHLOEvent, SendGETRequestEvent, CloseConnectionEvent, SendFullCHLOEvent, \
    ZeroRTTCHLOEvent, ResetEvent
from sniffer.sniffer import Sniffer
from util.NonDeterminismCatcher import NonDeterminismCatcher
from util.RespondDummy import RespondDummy
from util.SessionInstance import SessionInstance
from util.packet_to_hex import extract_from_packet, extract_from_packet_as_bytestring
from util.split_at_every_n import split_at_nth_char
from util.string_to_ascii import string_to_ascii_old, string_to_ascii
import time
import logging
import os


# header lenght: 22 bytes
DPORT = 443


class Scapy:

    sniffer = None
    learner = None
    response_times = []
    processed = False
    start_time = None
    TIMEOUT = 0.3263230323791504 * 5
    result = ""
    logger = None
    run_results = []
    current_event = None
    run = ""
    ndc = None
    first_time = True
    run_events = []
    previous_result = ""

    def __init__(self) -> None:
        currenttime = datetime.datetime.now().strftime("%I:%M%p on %B %d, %Y")
        filename = 'log_{}.txt'.format(currenttime)
        # logging.basicConfig(filename=filename, level=logging.INFO, format='%(asctime)s %(levelname)s %(name)s %(message)s')
        self.logger = logging.getLogger(__name__)

        # self.sniffer = Sniffer()
        # self.sniffer.start()
        # self.sniffer.set_session_instance(PacketNumberInstance.get_instance(), self.logger)
        # # self.ndc = NonDeterminismCatcher(self.logger)

        dhke.set_up_my_keys()
        self.learner = RespondDummy()
        try:
            pass
            # self.learner = LearnerConnectionInstance()
            # self.learner.add_observer(self)
            # self.learner.set_up_communication_server()
        except Exception as err:
            self.logger.exception(err)

    def reset(self, reset_server, reset_run=True):
        # also reset the server
        if reset_server:
            # remove the previous session
            CacheInstance.get_instance().remove_session_model()
            filename = str(time.time())
            open('resets/{}'.format(filename), 'a')
            time.sleep(8)

        if reset_run:
            # For the three times a command we do not want to remove the run events, only when there is a complete reset
            # which occurs after an iteration or after an explicit RESET command.
            self.run_events = []

        self.run = ""
        self.previous_result = ""
        # PacketNumberInstance.get_instance().reset()
        conn_id = random.getrandbits(64)
        SessionInstance.get_instance().shlo_received = False
        SessionInstance.get_instance().scfg = ""
        SessionInstance.get_instance().zero_rtt = False
        self.logger.info("Changing CID from {}".format(
            SessionInstance.get_instance().connection_id))
        SessionInstance.get_instance().connection_id_as_number = conn_id
        SessionInstance.get_instance().connection_id = str(
            format(conn_id, 'x').zfill(16))  # Pad to 16 chars
        self.logger.info("To {}".format(
            SessionInstance.get_instance().connection_id))

    def send_chlo(self, only_reset):
        # print("Only reset? {}".format(only_reset))
        self.reset(only_reset)

        chlo = QUICHeader()
        conf.L3socket = L3RawSocket
        chlo.setfieldval('CID', string_to_ascii(
            SessionInstance.get_instance().connection_id))
        packet_number = PacketNumberInstance.get_instance().get_next_packet_number()
        chlo.setfieldval("Packet_Number", string_to_ascii(str("%08x" % packet_number)))

        associated_data = extract_from_packet(chlo, end=18)
        body = extract_from_packet(chlo, start=30)

        message_authentication_hash = FNV128A().generate_hash(associated_data, body)
        chlo.setfieldval('Message_Authentication_Hash',
                         string_to_ascii(message_authentication_hash))

        SessionInstance.get_instance().chlo = extract_from_packet_as_bytestring(
            chlo, start=34, end=1058)

        p = IP(dst=SessionInstance.get_instance().destination_ip) / \
            UDP(dport=DPORT, sport=61250) / chlo
        ans, unans = sr(p,timeout=self.TIMEOUT)
        try:
            packet = bytes(ans[0][1][UDP][Raw])
            # print(packet)
            packet_type = packet[34:37].decode()
        
            tag_name_start_index = 42

            if packet_type == "REJ":
                self.server_adress_token = packet[tag_name_start_index +
                                                8*7:tag_name_start_index+8*7+60]
                self.server_nonce = packet[tag_name_start_index +
                                        8*7+60:tag_name_start_index+8*7+60+56]
                self.server_config_id = packet[tag_name_start_index+8*7+60+56+256+8+16 +
                                                8+8+8+8+8+8+24+4:tag_name_start_index+8*7+60+56+256+8+16+8+8+8+8+8+8+24+4+16]
                SCFG = packet[tag_name_start_index+8*7 +
                    60+56+256:tag_name_start_index+8*7+60+56+256+175]
                CERT = packet[tag_name_start_index+8*7+60+56+256+175+12:tag_name_start_index+8*7+60+56+256+175+12+696]
                SessionInstance.get_instance().server_nonce = self.server_nonce.hex()
                SessionInstance.get_instance().source_address_token = self.server_adress_token
                SessionInstance.get_instance().server_config_id=self.server_config_id.hex()
                SessionInstance.get_instance(
                ).scfg = packet[tag_name_start_index+8*7+60+56+256:tag_name_start_index+8*7+60+56+256+175].hex()
                # SessionInstance.get_instance().cert = packet[tag_name_start_index+8*7+60+56+256+175+12:tag_name_start_index+8*7+60+56+256+175+12+696]
            PUBS = packet[tag_name_start_index+8*7+60+56+256+8+16+8+8+8+8+8+8+24 +
                        4+16+4:tag_name_start_index+8*7+60+56+256+8+16+8+8+8+8+8+8+24+4+16+4+35]
            SessionInstance.get_instance().peer_public_value_initial = bytes.fromhex(PUBS[3:].hex())
            return packet
        except:
            return b"EXP"

    def wait_for_signal_or_expiration(self):
        # wait for a specific time otherwise
        start = time.time()
        expired = False
        print(self.run_results)
        while not self.processed and not expired:
            if time.time() - start >= self.TIMEOUT:
                expired = True
        if expired:
            # print("General expired")
            if len(self.run_results) == 3 or True:
                try:
                    # Get the majority element
                    c = Counter(self.run_results)
                    value, count = c.most_common()[0]
                    self.learner.respond(value)
                    self.logger.info("General expired")
                    self.run += str(self.current_event)
                    # self.ndc.add_run(self.run, value)
                    self.run_results = []
                    self.previous_result = value
                    # self.reset(True, False)    # Reset the server
                except:
                    pass
            else:
                self.run_results.append("EXP")
                self.logger.info(
                    "Received first time {} launching again".format("EXP"))
                time.sleep(2)
                self.logger.info("Run events {}".format(self.run_events))
                # if not isinstance(self.current_event, SendGETRequestEvent):
                self.send(self.current_event, True)
        else:
            # print("General response {}".format(self.result))
            self.logger.info(
                "Currently at run results {}".format(self.run_results))
            self.logger.info(
                "Current running event {}".format(self.current_event))
            self.logger.info("Previous result {}".format(self.previous_result))
            if isinstance(self.current_event, CloseConnectionEvent):
                if self.previous_result == "EXP":
                    self.learner.respond("closed")
                    self.run_results = []
                    return
            if isinstance(self.current_event, SendGETRequestEvent):
                # Does not need multiple times, as only the first time we get an HTTP response
                if self.previous_result == "EXP":
                    self.learner.respond("EXP")
                    self.run_results = []
                    return
                elif self.result == "REJ":
                    self.learner.respond("EXP")
                    self.run_results = []
                    self.result = ""
                    return
                elif self.previous_result == "shlo":
                    self.learner.respond("http")
                    self.previous_result = "http"
                    self.result = ""
                    self.run_results = []
                    return
                else:
                    self.learner.respond(self.result)
                    self.previous_result = self.result
                    self.result = ""
                    self.run_results = []
                    return
            if isinstance(self.current_event, SendInitialCHLOEvent):
                # Does not really need to send multiple times
                if self.previous_result == "":
                    self.learner.respond("REJ")
                    self.previous_result = "REJ"
                    self.result = ""
                    self.run_results = []
                    return
            if isinstance(self.current_event, SendFullCHLOEvent):
                # If it is a full CHLO and we receive a SHLO. Do not send it again
                if self.result == "http":
                    self.learner.respond("EXP")
                    self.result = ""
                    self.run_results = []
                    return

                elif self.previous_result == "EXP":
                    self.learner.respond("EXP")
                    self.previous_result = "EXP"
                    self.run_results = []
                    return

                elif self.previous_result == "shlo":
                    self.learner.respond("EXP")
                    self.previous_result = "EXP"
                    self.run_results = []
                    return

                elif self.previous_result == "" or self.previous_result == "PRST":
                    self.learner.respond("PRST")
                    self.result = ""
                    self.run_results = []
                    return

                if self.result == "shlo":
                    self.learner.respond("shlo")
                    self.run_results = []
                    self.previous_result = "shlo"
                    return

            if isinstance(self.current_event, ZeroRTTCHLOEvent):
                # We can only send this once, otherwise the second time it will automatically send it as a full message
                SessionInstance.get_instance().currently_sending_zero_rtt = False
                if self.result == "REJ" or self.result == "shlo":
                    self.learner.respond(self.result)
                    self.previous_result = self.result
                    self.run_results = []
                    return

            if len(self.run_results) == 2 and isinstance(self.current_event, ZeroRTTCHLOEvent):
                # We actually need it, otherwise a subsequent Full CHLO will not result in a SHLO.
                SessionInstance.get_instance().currently_sending_zero_rtt = False
            if len(self.run_results) == 3 or True:
                # Get the majority element
                if self.run_results.count("EXP") < 3:
                    self.run_results = [
                        x for x in self.run_results if 'EXP' != x]
                c = Counter(self.run_results)
                value, count = c.most_common()[0]
                if isinstance(self.current_event, SendGETRequestEvent):
                    # If there is atleast one HTTP response, then it is a HTTP response.
                    # Because the server didn't respond to three subsequent GET requests.
                    if "http" in self.run_results:
                        value = "http"
                    if "REJ" in self.run_results:
                        if self.run_results.count("REJ") == len(self.run_results):
                            value = "EXP"
                        else:
                            # remove all the REJs
                            value = list(
                                filter(lambda a: a != 'REJ', self.run_results))[0]
                    if self.previous_result == "shlo" and "http" not in self.run_results:
                        value = "http"

                if isinstance(self.current_event, ZeroRTTCHLOEvent):
                    SessionInstance.get_instance().currently_sending_zero_rtt = False
                    if value == "REJ":
                        SessionInstance.get_instance().zero_rtt = False
                    elif value == "shlo":
                        SessionInstance.get_instance().zero_rtt = True

                    if self.previous_result == "REJ" and value != "shlo":
                        value = "shlo"
                    elif self.previous_result == "shlo":
                        value = "shlo"
                    elif value == "EXP":
                        value = "REJ"

                self.learner.respond(value)
                self.logger.info("Responding to learner {}".format(value))
                # self.run += str(self.current_event)
                # self.ndc.add_run(self.run, value)
                self.previous_result = value
                self.run_results = []
                # self.reset(True, False)
            else:
                self.run_results.append(self.result)
                self.logger.info(
                    "Received first time {} launching again".format(self.result))
                time.sleep(2)
                self.logger.info("Run events {}".format(self.run_events))
                # if not isinstance(self.current_event, SendGETRequestEvent):
                self.send(self.current_event, True)

        self.logger.info("=========== Request Finished ===========")
        self.result = ""

    def send_first_ack(self):
        chlo = ACKPacket()
        conf.L3socket = L3RawSocket

        chlo.setfieldval('CID', string_to_ascii(
            SessionInstance.get_instance().connection_id))
        chlo.setfieldval(
            "Packet_Number", PacketNumberInstance.get_instance().get_next_packet_number())

        # print("First Ack Packet Number {}".format(int(str(PacketNumberInstance.get_instance().highest_received_packet_number), 16)))
        chlo.setfieldval('Largest_Acked', int(
            str(PacketNumberInstance.get_instance().highest_received_packet_number), 16))
        chlo.setfieldval('First_Ack_Block_Length', int(
            str(PacketNumberInstance.get_instance().highest_received_packet_number), 16))

        # chlo.setfieldval('Largest Acked', 3)
        # chlo.setfieldval('First Ack Block Length', 3)

        associated_data = extract_from_packet(chlo, end=15)
        body = extract_from_packet(chlo, start=27)

        # print("Associated data {}".format(associated_data))
        # print("Body {}".format(body))

        message_authentication_hash = FNV128A().generate_hash(associated_data, body, True)
        chlo.setfieldval('Message_Authentication_Hash',
                         string_to_ascii(message_authentication_hash))

        # print("Sending first ACK...")

        p = IP(dst=SessionInstance.get_instance().destination_ip) / \
            UDP(dport=DPORT, sport=61250) / chlo
        send(p)

    def send_second_ack(self):
        chlo = SecondACKPacket()
        conf.L3socket = L3RawSocket

        chlo.setfieldval('CID', string_to_ascii(
            SessionInstance.get_instance().connection_id))
        chlo.setfieldval(
            "Packet_Number", PacketNumberInstance.get_instance().get_next_packet_number())

        associated_data = extract_from_packet(chlo, end=15)
        body = extract_from_packet(chlo, start=27)

        message_authentication_hash = FNV128A().generate_hash(associated_data, body)
        chlo.setfieldval('Message_Authentication_Hash',
                         string_to_ascii(message_authentication_hash))

        p = IP(dst=SessionInstance.get_instance().destination_ip) / \
            UDP(dport=DPORT, sport=61250) / chlo
        send(p)

    def send_ack_for_encrypted_message(self):
        ack = AckNotificationPacket()
        conf.L3socket = L3RawSocket

        ack.setfieldval('CID', string_to_ascii(
            SessionInstance.get_instance().connection_id))

        next_packet_number_int = PacketNumberInstance.get_instance().get_next_packet_number()
        next_packet_number_byte = int(
            next_packet_number_int).to_bytes(8, byteorder='little')
        next_packet_number_nonce = int(
            next_packet_number_int).to_bytes(2, byteorder='big')
        # print("Sending encrypted ack for packet number {}".format(next_packet_number_int))

        ack.setfieldval("Packet_Number", next_packet_number_int)
        highest_received_packet_number = format(int(
            PacketNumberInstance.get_instance().get_highest_received_packet_number(), 16), 'x')

        ack_body = "40"
        ack_body += str(highest_received_packet_number).zfill(2)
        ack_body += "0062"
        ack_body += str(highest_received_packet_number).zfill(2)
        ack_body += "00"
        # not sure yet if we can remove this?

        keys = SessionInstance.get_instance().keys

        request = {
            'mode': 'encryption',
            'input': ack_body,
            'key': keys['key1'].hex(),  # For encryption, we use my key
            # Fixed public flags 18 || fixed connection Id || packet number
            'additionalData': "18" + SessionInstance.get_instance().connection_id + next_packet_number_byte.hex()[:4],
            'nonce': keys['iv1'].hex() + next_packet_number_nonce.hex().ljust(16, '0')
        }

        # print("Ack request for encryption {}".format(request))

        ciphertext = CryptoConnectionManager.send_message(
            ConnectionEndpoint.CRYPTO_ORACLE, json.dumps(request).encode('utf-8'), True)
        ciphertext = ciphertext['data']
        # print("Ciphertext in ack {}".format(ciphertext))

        ack.setfieldval("Message_Authentication_Hash",
                        string_to_ascii(ciphertext[:24]))
        SessionInstance.get_instance().nr_ack_send += 1

        p = IP(dst=SessionInstance.get_instance().destination_ip) / UDP(dport=DPORT,
                                                                        sport=61250) / ack / Raw(load=string_to_ascii(ciphertext[24:]))
        send(p)

    def handle_received_encrypted_packet(self, packet):
        a = AEADPacketDynamic(packet[0][1][1].payload.load)
        a.parse()
        # print(">>>>>>>> Received packet with MAH: {}".format(a.get_field(AEADFieldNames.MESSAGE_AUTHENTICATION_HASH)))

        # Start key derixvation
        SessionInstance.get_instance().div_nonce = a.get_field(
            AEADFieldNames.DIVERSIFICATION_NONCE)
        SessionInstance.get_instance().message_authentication_hash = a.get_field(
            AEADFieldNames.MESSAGE_AUTHENTICATION_HASH)
        packet_number = a.get_field(AEADFieldNames.PACKET_NUMBER)
        SessionInstance.get_instance().packet_number = packet_number
        # print("Packet Number {}".format(packet_number))
        SessionInstance.get_instance().largest_observed_packet_number = packet_number
        SessionInstance.get_instance().associated_data = a.get_associated_data()
        # print("Associated Data {}".format(SessionInstance.get_instance().associated_data))
        ciphertext = split_at_nth_char(
            a.get_field(AEADFieldNames.ENCRYPTED_FRAMES))

        # print("Received peer public value {}".format(SessionInstance.get_instance().peer_public_value))
        dhke.generate_keys(SessionInstance.get_instance(
        ).peer_public_value, SessionInstance.get_instance().shlo_received)
        # SessionInstance.get_instance().packet_number = packet_number

        # Process the streams
        processor = FramesProcessor(ciphertext)
        processor.process()

    def send_ping(self):
        print("Sending ping message...")
        ping = PingPacket()
        ping.setfieldval('CID', string_to_ascii(
            SessionInstance.get_instance().connection_id))

        packet_number = PacketNumberInstance.get_instance().get_next_packet_number()
        ciphertext = CryptoManager.encrypt(bytes.fromhex(
            "07"), packet_number, SessionInstance.get_instance())

        ping.setfieldval('Packet_Number', packet_number)
        ping.setfieldval("Message_Authentication_Hash",
                         string_to_ascii(ciphertext[:24]))

        conf.L3socket = L3RawSocket
        p = IP(dst=SessionInstance.get_instance().destination_ip) / UDP(dport=DPORT,
                                                                        sport=61250) / ping / Raw(load=string_to_ascii(ciphertext[24:]))
        # Maybe we cannot assume that is just a version negotiation packet?
        send(p)

    def send_full_chlo(self):
        fullchlo = FullCHLOPacket()

        fullchlo.setfieldval('CID', string_to_ascii(
            SessionInstance.get_instance().connection_id))
        packet_number = PacketNumberInstance.get_instance().get_next_packet_number()
        fullchlo.setfieldval("Packet_Number", string_to_ascii(str("%08x" % packet_number)))
        fullchlo.setfieldval('STK_Value', string_to_ascii(
            SessionInstance.get_instance().source_address_token.hex()))
        fullchlo.setfieldval(
            'SNO_Value', string_to_ascii(SessionInstance.get_instance().server_nonce))
        fullchlo.setfieldval('SCID_Value', string_to_ascii(
            SessionInstance.get_instance().server_config_id))  # incomplete

        epochtime = str(hex(int(time.time())))
        epoch = ''.join([epochtime[i:i+2]
                        for i in range(0, len(epochtime), 2)][1:][::-1])
        sORBIT = '0'*16
        randomString = bytes.hex(os.urandom(20))
        NONC = epoch + sORBIT + randomString
        fullchlo.setfieldval('NONC_Value', string_to_ascii(NONC))
        SessionInstance.get_instance().client_nonce = NONC
# #         # Lets just create the public key for DHKE
        dhke.set_up_my_keys()

        fullchlo.setfieldval('PUBS_Value', string_to_ascii(
            SessionInstance.get_instance().public_values_bytes))  # incomplete

        associated_data = extract_from_packet(fullchlo, end=18)
        body = extract_from_packet(fullchlo, start=30)

        message_authentication_hash = FNV128A().generate_hash(associated_data, body)
        fullchlo.setfieldval('Message_Authentication_Hash',
                             string_to_ascii(message_authentication_hash))

        conf.L3socket = L3RawSocket
        # CHLO from the CHLO tag, which starts at offset 26 (22 header + frame type + stream id + offset)
        SessionInstance.get_instance().chlo = extract_from_packet_as_bytestring(
            fullchlo, start=36, end=1060)

        p = IP(dst=SessionInstance.get_instance().destination_ip) / \
                UDP(dport=DPORT, sport=61250) / fullchlo

        ans, unans = sr(p, timeout=self.TIMEOUT)
        try:
            packet = bytes(ans[0][1][UDP][Raw])

            ciphertext = packet[18+32:]
            div_nonce = packet[18:18+32]
            packet_number=packet[17]
            if SessionInstance.get_instance().shlo_received == False:
                dhke.generate_keys(SessionInstance.get_instance().peer_public_value_initial, SessionInstance.get_instance().shlo_received)
            diversed_key = dhke.diversify(SessionInstance.get_instance().initial_keys['key2'], SessionInstance.get_instance().initial_keys['iv2'], div_nonce)
            aesg_nonce = diversed_key['diversified_iv'] + packet_number.to_bytes(8, byteorder='little')
            addData = packet[:50]
            try:
                decoder = AES.new(diversed_key['diversified_key'], AES.MODE_GCM, aesg_nonce, mac_len=12)
                decoder = decoder.update(addData)
                plain_text = decoder.decrypt_and_verify(ciphertext[:-12],ciphertext[-12:])
            except:
                return b"ERROR"
            if plain_text[6:10] == b"SHLO":
                SessionInstance.get_instance().shlo_received = True
            value_start = 94
            SessionInstance.get_instance().peer_public_value_final = plain_text[value_start + int.from_bytes(plain_text[50:54], "little"):value_start + int.from_bytes(plain_text[58:62], "little")]
            SessionInstance.get_instance().server_nonce = plain_text[value_start + int.from_bytes(plain_text[18:22], "little") : value_start + int.from_bytes(plain_text[26:30], "little")].hex()

            dhke.generate_keys(SessionInstance.get_instance().peer_public_value_final, True)
            
            return plain_text
        except:
            return b"EXP"
        
    def get(self):
        frame_data = "a003002400001b012500000005000000000f8287844186a0e41d139d097a89a11dad311869702e1f"
        
        packet_number = PacketNumberInstance.get_instance().get_next_packet_number()
        try:
            nonce = SessionInstance.get_instance().final_keys['iv1'] + packet_number.to_bytes(8, byteorder='little')
            encoder = AES.new(SessionInstance.get_instance().final_keys['key1'], AES.MODE_GCM, nonce, mac_len=12)
            addData = bytes.fromhex("41"+SessionInstance.get_instance().connection_id+ packet_number.to_bytes(2, byteorder='big').hex())
            encoder = encoder.update(addData)
            ciphertext = encoder.encrypt_and_digest(bytes.fromhex(frame_data))
        except:
            return b"PRST"

        get = AEADRequestPacket()
        get.setfieldval("Public_Flags", 0x41)
        get.setfieldval('CID', string_to_ascii(SessionInstance.get_instance().connection_id))
        get.setfieldval('Packet_Number', packet_number.to_bytes(2, byteorder='big'))

        p = IP(dst=SessionInstance.get_instance().destination_ip) / UDP(dport=DPORT, sport=61250) / get / Raw(load=ciphertext[0]+ciphertext[1])
        ans, unans = sr(p, timeout=self.TIMEOUT)
        try:
            packet = bytes(ans[0][1][UDP][Raw])
            packet_number = packet[1]
            ciphertext = packet[2:]
            addData = packet[:2]
            aesg_nonce = SessionInstance.get_instance().final_keys['iv2'] + packet_number.to_bytes(8, byteorder='little')
            try:
                decoder = AES.new(SessionInstance.get_instance().final_keys['key2'], AES.MODE_GCM, aesg_nonce, mac_len=12)
                decoder = decoder.update(addData)
                plain_text = decoder.decrypt_and_verify(ciphertext[:-12],ciphertext[-12:])
            except:
                return b"ERROR"
            return plain_text[44:48]
        except:
            return b"EXP"

    def close_connection(self):
        """
        We do this the unfriendly way, since GoAway does not work. friendly way by means of a Go Away
        :return:
        """
        frame_data = "02000000010000"
        packet_number = PacketNumberInstance.get_instance().get_next_packet_number()
        error = False
        try:
            # encrypt it
            aesg_nonce = SessionInstance.get_instance().final_keys['iv1'] + packet_number.to_bytes(8, byteorder='little')
            encoder = AES.new(SessionInstance.get_instance().final_keys['key1'], AES.MODE_GCM, aesg_nonce, mac_len=12)
            addData = bytes.fromhex("40"+SessionInstance.get_instance().connection_id+ packet_number.to_bytes(1, byteorder='little').hex())
            encoder = encoder.update(addData)
            ciphertext = encoder.encrypt_and_digest(bytes.fromhex(frame_data))
        except:
            error = True
        a = AEADRequestPacketClose()
        a.setfieldval("Public_Flags", 0x40)
        a.setfieldval('CID', string_to_ascii(SessionInstance.get_instance().connection_id))
        a.setfieldval('Packet_Number', string_to_ascii(str("%02x" % packet_number)))
        if error:
            p = IP(dst=SessionInstance.get_instance().destination_ip) / UDP(dport=DPORT, sport=61250) / a / Raw(load=string_to_ascii(frame_data))
        else:
            p = IP(dst=SessionInstance.get_instance().destination_ip) / UDP(dport=DPORT, sport=61250) / a / Raw(load=ciphertext[0]+ciphertext[1])
        ans, unans = sr(p, timeout=self.TIMEOUT)
        try:
            packet = bytes(ans[0][1][UDP][Raw])
            packet_number = packet[1]
            ciphertext = packet[2:]
            addData = packet[:2]
            aesg_nonce = SessionInstance.get_instance().final_keys['iv2'] + packet_number.to_bytes(8, byteorder='little')
            try:
                decoder = AES.new(SessionInstance.get_instance().final_keys['key2'], AES.MODE_GCM, aesg_nonce, mac_len=12)
                decoder = decoder.update(addData)
                plain_text = decoder.decrypt_and_verify(ciphertext[:-12],ciphertext[-12:])
            except:
                return b"ERROR"
            if plain_text == b'\x02\x00\x00\x00\x10\x00\x00':
                return b"closed"
            else:
                return b"ERROR"
        except:
            return b"EXP"

    def send_full_chlo_to_existing_connection(self):
        """
        Is it sent encrypted?
        :return:
        """
        try:
            previous_session = SessionModel.get(SessionModel.id == 1)
            self.logger.info(previous_session)
            self.logger.info("Server config Id {}".format(
                previous_session.server_config_id))
            self.logger.info(SessionInstance.get_instance().app_keys)
            # I want to force the sniffer to generate a new set of keys.
            SessionInstance.get_instance().last_received_rej = "-1"
            SessionInstance.get_instance().zero_rtt = True

            # The order is important!
            tags = [
                {
                    'name': 'PAD',
                    'value': '00000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000'
                },
                {
                    'name': 'SNI',
                    'value': '7777772e6578616d706c652e6f7267'
                },
                {
                    'name': 'STK',
                    'value': previous_session.source_address_token
                },
                {
                    'name': 'SNO',
                    'value': previous_session.server_nonce
                },
                {
                    'name': 'VER',
                    'value': '00000000'
                },
                {
                    'name': 'CCS',
                    'value': '01e8816092921ae87eed8086a2158291'
                },
                {
                    'name': 'NONC',
                    'value': '5ac349e90091b5556f1a3c52eb57f92c12640e876e26ab2601c02b2a32f54830'
                },
                {
                    'name': 'AEAD',
                    'value': '41455347'  # AESGCM12
                },
                {
                    'name': 'SCID',
                    'value': previous_session.server_config_id
                },
                {
                    'name': 'PDMD',
                    'value': '58353039'
                },
                {
                    'name': 'ICSL',
                    'value': '1e000000'
                },
                {
                    'name': 'PUBS',
                    'value': '96D49F2CE98F31F053DCB6DFE729669385E5FD99D5AA36615E1A9AD57C1B090C'
                },
                {
                    'name': 'MIDS',
                    'value': '64000000'
                },
                {
                    'name': 'KEXS',
                    'value': '43323535'  # C25519
                },
                {
                    'name': 'XLCT',
                    'value': '8d884a6c79a0e6de'
                },
                {
                    'name': 'CFCW',
                    'value': '00c00000'
                },
                {
                    'name': 'SFCW',
                    'value': '00800000'
                },
            ]

            d = DynamicCHLOPacket(tags)
            body = d.build_body()
            PacketNumberInstance.get_instance().reset()

            conn_id = random.getrandbits(64)
            SessionInstance.get_instance().server_nonce = previous_session.server_nonce
            SessionInstance.get_instance().connection_id_as_number = conn_id
            SessionInstance.get_instance().connection_id = str(format(conn_id, 'x').zfill(8))
            SessionInstance.get_instance().peer_public_value = bytes.fromhex(
                previous_session.public_value)
            self.logger.info("Using connection Id {}".format(
                SessionInstance.get_instance().connection_id))
            SessionInstance.get_instance().shlo_received = False
            # SessionInstance.get_instance().zero_rtt = True  # This one should only be set if the Zero RTT CHLO does not result in a REJ.
            #
            a = FullCHLOPacketNoPadding()
            a.setfieldval(
                'Packet Number', PacketNumberInstance.get_instance().get_next_packet_number())
            a.setfieldval('CID', string_to_ascii(
                SessionInstance.get_instance().connection_id))

            # # Lets just create the public key for DHKE
            dhke.set_up_my_keys()

            associated_data = extract_from_packet(a, end=15)
            body_mah = [body[i:i + 2] for i in range(0, len(body), 2)]
            message_authentication_hash = FNV128A().generate_hash(associated_data, body_mah)

            conf.L3socket = L3RawSocket
            SessionInstance.get_instance().chlo = extract_from_packet_as_bytestring(a,
                                                                                    start=27)  # CHLO from the CHLO tag, which starts at offset 26 (22 header + frame type + stream id + offset)
            SessionInstance.get_instance().chlo += body[4:]

            # dhke.generate_keys(bytes.fromhex(previous_session.public_value), False)
            # ciphertext = CryptoManager.encrypt(bytes.fromhex(SessionInstance.get_instance().chlo), 1)
            #
            a.setfieldval('Message_Authentication_Hash',
                          string_to_ascii(message_authentication_hash))
            #
            # print("Send full CHLO from existing connection")
            #
            p = IP(dst=SessionInstance.get_instance().destination_ip) / UDP(dport=DPORT, sport=61250) / a / Raw(
                load=string_to_ascii(body))
            # # Maybe we cannot assume that is just a version negotiation packet?
            # self.sniffer.add_observer(self)
            send(p)
            self.wait_for_signal_or_expiration()

            self.processed = False
            # self.sniffer.remove_observer(self)
        except Exception:
            self.send_chlo(False)

    def send_encrypted_request(self):
        """
        Make an AEAD GET Request to example.org
        :return:
        """
        self.logger.info("Making GET Request")

        # Generate forward secure keys if it hasn't already been done.
        current_app_key = SessionInstance.get_instance().app_keys
        if current_app_key['type'] != "FORWARD" or current_app_key['mah'] != SessionInstance.get_instance().last_received_shlo:
            if len(SessionInstance.get_instance().peer_public_value) == 0:
                pass
            else:
                key = dhke.generate_keys(
                    SessionInstance.get_instance().peer_public_value, True, self.logger)
                SessionInstance.get_instance().app_keys['type'] = "FORWARD"
                SessionInstance.get_instance(
                ).app_keys['mah'] = SessionInstance.get_instance().last_received_shlo
                SessionInstance.get_instance().app_keys['key'] = key

        get_request = "800300002501250000000500000000FF418FF1E3C2E5F23A6BA0AB9EC9AE38110782848750839BD9AB7A85ED6988B4C7"

        packet_number = PacketNumberInstance.get_instance().get_next_packet_number()
        ciphertext = CryptoManager.encrypt(bytes.fromhex(
            get_request), packet_number, SessionInstance.get_instance(), self.logger)

        # Send it to the server
        a = AEADRequestPacket()
        a.setfieldval('CID', string_to_ascii(
            SessionInstance.get_instance().connection_id))
        a.setfieldval("Public_Flags", 0x18)
        a.setfieldval('Packet_Number', packet_number)
        a.setfieldval("Message_Authentication_Hash",
                      string_to_ascii(ciphertext[0:24]))

        p = IP(dst=SessionInstance.get_instance().destination_ip) / UDP(dport=DPORT, sport=61250) / a / Raw(
            load=string_to_ascii(ciphertext[24:]))
        # self.sniffer.add_observer(self)
        send(p)
        self.wait_for_signal_or_expiration()
        self.processed = False
        # self.sniffer.remove_observer(self)

    # def stop_sniffer(self):
    #     self.sniffer.stop_sniffing()

    def send(self, command, deterministic_repeat=False):
        try:
            if isinstance(command, ResetEvent):
                print("Resetting received")
                return self.send_chlo(True)
            if isinstance(command, SendInitialCHLOEvent):
                print("Sending CHLO")
                return self.send_chlo(False)
            elif isinstance(command, SendFullCHLOEvent):
                print("Sending Full CHLO")
                return self.send_full_chlo()
            elif isinstance(command, ZeroRTTCHLOEvent):
                print("Sending Zero RTT CHLO")
                return self.send_full_chlo_to_existing_connection()
            elif isinstance(command, SendGETRequestEvent):
                print("Sending GET")
                return self.get()
            elif isinstance(command, CloseConnectionEvent):
                print("Sending CLOSE")
                return self.close_connection()
            else:
                print("Unknown command {}".format(command))
        except Exception as err:
            self.logger.exception(err)

    def update(self, event, result):
        try:
            self.logger.info("Scapy Received result {}".format(result))
            self.result = result
            self.processed = True
        except Exception as err:
            self.logger.error(err)


s = Scapy()
s.send(SendInitialCHLOEvent())
# time.sleep(10)
s.send(SendFullCHLOEvent())
# s.send(SendGETRequestEvent())
s.send(CloseConnectionEvent())
# s.send(SendFullCHLOEvent())
# s.send(ZeroRTTCHLOEvent())

# try:
#     operations = [(s.send_chlo, False), (s.send_full_chlo, True), (s.send_full_chlo_to_existing_connection, True), (s.send_encrypted_request, True), (s.close_connection, True), (s.reset, False)]
#     print("Starting now {}".format(time.time()))
#     for i in range(2):
#         random.shuffle(operations)
#         for operation, encrypted in operations:
#             print("PERFORMING OPERATION {}".format(operation))
#             operation()
#             print("FINISHED OPERATION {}".format(operation))
#             time.sleep(2)
# except:
#     print("Fail")
# # print("Done?!")
# times = []
# for i in tqdm(range(10)):
#     s.logger.info(">>>>>>>>>>>> Starting with round {}".format(i))
#     s.logger.info("Resetting")
#     s.send(ResetEvent())
#     start = time.time()
#     # s.send(SendInitialCHLOEvent())
#     # s.send(SendGETRequestEvent())
#     # s.send(CloseConnectionEvent())
#     times.append(time.time()-start)
#     s.send(CloseConnectionEvent())
#     s.logger.info("Currently at {} out of 10".format(i))

# times = sorted(times)
# s.logger.info("All execution times {}".format(times))
# s.logger.info("Median execution time is {}".format(median(times)))

# s.send(ResetEvent())
# s.send(ZeroRTTCHLOEvent())
#     s.send(SendGETRequestEvent())
#     s.send(ResetEvent())
# s.send(ZeroRTTCHLOEvent())
# s.send(SendGETRequestEvent())
# s.send(SendFullCHLOEvent())
# s.send(SendInitialCHLOEvent())
# s.send(SendInitialCHLOEvent())
# s.send(SendFullCHLOEvent())
# s.send(ZeroRTTCHLOEvent())
# s.send(ZeroRTTCHLOEvent())
# s.send(ZeroRTTCHLOEvent())
# s.send(SendGETRequestEvent())
# s.send(SendInitialCHLOEvent())
