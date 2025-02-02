#! /usr/bin/env python3
# -*- coding: UTF-8 -*-
#
# Copyright (c) 2014, Dionach Ltd. All rights reserved. See LICENSE file.
#
# PANhunt: search directories and sub directories for documents with PANs
# By BB
#
# Contributors: Zafer Balkan, 2023

import logging
import os
import struct
from datetime import datetime, timedelta
from io import BufferedReader
from typing import Literal, Optional, Union

import panutils
from enums import PropIdEnum, PTypeEnum
from exceptions import PANHuntException

_FilePathOrFileObject = Union[BufferedReader, int, str, bytes, os.PathLike[
    str], os.PathLike[bytes]]

_ValueType = Optional[Union[int, float, datetime, bool, str, bytes,
                            list[int], list[float], list[datetime], list[bytes], list[str]]]

###################################################################################################################################
#  __  __ ____         ____ _____ ____
# |  \/  / ___|       / ___|  ___| __ )
# | |\/| \___ \ _____| |   | |_  |  _ \
# | |  | |___) |_____| |___|  _| | |_) |
# |_|  |_|____/       \____|_|   |____/
#
###################################################################################################################################


class FAT:

    DIFSECT = 0xFFFFFFFC
    FATSECT = 0xFFFFFFFD
    ENDOFCHAIN = 0xFFFFFFFE
    FREESECT = 0xFFFFFFFF

    mscfb: 'MSCFB'
    entries: list[int]

    def __init__(self, mscfb: 'MSCFB') -> None:

        self.mscfb = mscfb  # Microsoft Compound File Binary File
        difat_index: int = 0
        self.entries = []
        while mscfb.DIFAT[difat_index] != FAT.FREESECT:
            sector: int = mscfb.DIFAT[difat_index]
            sector_bytes: bytes = mscfb.get_sector_bytes(sector)
            format: str = 'I' * (mscfb.SectorSize // 4)
            sector_fat_entries = struct.unpack(format, sector_bytes)
            self.entries.extend(sector_fat_entries)
            difat_index += 1

    def get_stream(self, sector: int, size: int) -> bytes:

        stream_bytes: bytes = b''
        while sector != FAT.ENDOFCHAIN:
            stream_bytes += self.mscfb.get_sector_bytes(sector)
            sector = self.entries[sector]
        if size > len(stream_bytes) or size < len(stream_bytes) - self.mscfb.SectorSize:
            raise PANHuntException(
                'FAT stream size does not match number of sectors')
        return stream_bytes[:size]

    def __str__(self) -> str:

        return ', '.join([f"{hex(sector)}:{hex(entry)}" for sector, entry in zip(list(range(len(self.entries))), self.entries)])


class MiniFAT:

    SECTORSIZE: int = 64

    entries: list[int]
    mscfb: 'MSCFB'
    mini_stream_bytes: bytes

    def __init__(self, mscfb: 'MSCFB') -> None:

        self.entries = []
        self.mscfb = mscfb
        self.mini_stream_bytes: bytes = b''

        current_sector: int = mscfb.FirstMiniFATSectorLocation
        for _ in range(mscfb.MiniFATSectors):
            sector_bytes: bytes = mscfb.get_sector_bytes(current_sector)
            current_sector = panutils.as_int(mscfb.fat.entries[current_sector])
            minifat_entries = struct.unpack(
                'I' * int(mscfb.SectorSize / 4), sector_bytes)
            self.entries.extend(minifat_entries)

    def get_all_mini_stream_fat_sectors(self) -> None:
        if self.mscfb.MiniStreamSectorLocation != FAT.ENDOFCHAIN:
            self.mini_stream_bytes = self.mscfb.fat.get_stream(
                self.mscfb.MiniStreamSectorLocation, self.mscfb.MiniStreamSize)

    def get_stream(self, sector: int, size: int) -> bytes:

        stream_bytes: bytes = b''
        while sector != FAT.ENDOFCHAIN:
            stream_bytes += self.mini_stream_bytes[sector *
                                                   MiniFAT.SECTORSIZE: sector * MiniFAT.SECTORSIZE + MiniFAT.SECTORSIZE]
            sector = self.entries[sector]
        if size > len(stream_bytes) or size < len(stream_bytes) - MiniFAT.SECTORSIZE:
            raise PANHuntException(
                'Mini FAT mini stream size does not match number of mini sectors')
        return stream_bytes[:size]

    def __str__(self) -> str:

        return ', '.join([f"{hex(sector)}:{hex(entry)}" for sector, entry in zip(list(range(len(self.entries))), self.entries)])


class Directory:

    mscfb: 'MSCFB'
    entries: list['DirectoryEntry']

    def __init__(self, mscfb: 'MSCFB') -> None:

        self.mscfb = mscfb
        self.entries = self.get_all_directory_entries(
            self.mscfb.FirstDirectorySectorLocation)
        self.set_entry_children(self.entries[0])  # recursive

    def get_all_directory_entries(self, start_sector: int) -> list['DirectoryEntry']:

        entries: list[DirectoryEntry] = []
        sector: int = start_sector
        while sector != FAT.ENDOFCHAIN:
            entries.extend(self.get_directory_sector(sector))
            sector = self.mscfb.fat.entries[sector]
        return entries

    def set_entry_children(self, dir_entry: 'DirectoryEntry') -> None:

        dir_entry.children = {}
        child_ids_queue: list[int] = []
        if dir_entry.ChildID != DirectoryEntry.NOSTREAM:
            child_ids_queue.append(dir_entry.ChildID)
            while child_ids_queue:
                child_entry: DirectoryEntry = self.entries[child_ids_queue.pop(
                )]
                if child_entry.Name in list(dir_entry.children.keys()):
                    raise PANHuntException(
                        'Directory Entry Name already in children dictionary')
                dir_entry.children[child_entry.Name] = child_entry
                if child_entry.SiblingID != DirectoryEntry.NOSTREAM:
                    child_ids_queue.append(child_entry.SiblingID)
                if child_entry.RightSiblingID != DirectoryEntry.NOSTREAM:
                    child_ids_queue.append(child_entry.RightSiblingID)
                if child_entry.ChildID != DirectoryEntry.NOSTREAM:
                    self.set_entry_children(child_entry)

    def get_directory_sector(self, sector: int) -> list['DirectoryEntry']:

        entries: list['DirectoryEntry'] = []
        sector_bytes: bytes = self.mscfb.get_sector_bytes(sector)
        sector_directory_entry_count: int = int(self.mscfb.SectorSize / 128)
        for i in range(sector_directory_entry_count):
            entries.append(DirectoryEntry(
                self.mscfb, sector_bytes[DirectoryEntry.ENTRY_SIZE * i:DirectoryEntry.ENTRY_SIZE * i + DirectoryEntry.ENTRY_SIZE]))
        return entries

    def __str__(self) -> str:

        return ', '.join([str(entry) for entry in self.entries])


class DirectoryEntry:

    ENTRY_SIZE = 128
    OBJECT_UNKNOWN = 0x0
    OBJECT_STORAGE = 0x1  # folder
    OBJECT_STREAM = 0x2  # file
    OBJECT_ROOT_STORAGE = 0x5
    NOSTREAM = 0xFFFFFFFF

    mscfb: 'MSCFB'
    ObjectType: int
    ColorFlag: int
    SiblingID: int
    RightSiblingID: int
    ChildID: int
    CLSID: bytes
    StateBits: int
    CreationTime: Optional[datetime]
    ModifiedTime: Optional[datetime]
    StreamSize: int
    StartingSectorLocation: int
    stream_data: bytes
    children: dict[str, 'DirectoryEntry']

    def __init__(self, mscfb: 'MSCFB', directory_bytes: bytes) -> None:

        raw_size: int = len(directory_bytes)
        if raw_size != DirectoryEntry.ENTRY_SIZE:
            # raise MSGException('Directory Entry not 128 bytes')
            # print('Directory Entry not 128 bytes')
            return

        self.mscfb = mscfb
        nameLength: int = panutils.unpack_integer('H', directory_bytes[64:66])
        if nameLength > 64:
            # raise MSGException('Directory Entry name cannot be longer than 64')
            # print('Directory Entry name cannot be longer than 64')
            return
        self.Name: str = directory_bytes[:nameLength -
                                         2].decode('utf-16-le')
        self.ObjectType, self.ColorFlag = struct.unpack(
            'BB', directory_bytes[66:68])
        self.SiblingID, self.RightSiblingID, self.ChildID = struct.unpack(
            'III', directory_bytes[68:80])
        self.CLSID = panutils.unpack_bytes('16s', directory_bytes[80:96])
        self.StateBits = panutils.unpack_integer('I', directory_bytes[96:100])
        creation_time_bytes, modified_time_bytes = struct.unpack(
            '8s8s', directory_bytes[100:116])
        if creation_time_bytes == '\x00' * 8:
            self.CreationTime = None
        else:
            self.CreationTime = panutils.bytes_to_time(creation_time_bytes)
        if modified_time_bytes == '\x00' * 8:
            self.ModifiedTime = None
        else:
            self.ModifiedTime = panutils.bytes_to_time(modified_time_bytes)
        self.StartingSectorLocation = panutils.unpack_integer(
            'I', directory_bytes[116:120])
        self.StreamSize = panutils.unpack_integer(
            'Q', directory_bytes[120:128])
        if mscfb.MajorVersion == 3:
            self.StreamSize = self.StreamSize & 0xFFFFFFFF  # upper 32 bits may not be zero
        self.children = {}

    def __cmp__(self, other: 'DirectoryEntry') -> bool:
        return self.Name == other.Name

    def get_data(self) -> bytes:

        if self.ObjectType != DirectoryEntry.OBJECT_STREAM:
            raise PANHuntException('Directory Entry is not a stream object')
        if self.StreamSize < self.mscfb.MiniStreamCutoffSize:  # Mini FAT stream
            self.stream_data = self.mscfb.minifat.get_stream(
                self.StartingSectorLocation, self.StreamSize)
        else:  # FAT
            self.stream_data = self.mscfb.fat.get_stream(
                self.StartingSectorLocation, self.StreamSize)
        return self.stream_data

    def list_children(self, level: int = 0, expand: bool = False) -> str:

        line_pfx: str = '\t' * level
        s: str = ''
        sorted_entries: list['DirectoryEntry'] = [
            i for i in self.children.values()]
        sorted_entries.sort(key=lambda x: x.Name)
        for child_entry in sorted_entries:
            line_sfx: str = ''
            if child_entry.ObjectType == DirectoryEntry.OBJECT_STORAGE:
                line_sfx = f"({len(list(child_entry.children.keys()))})"
            s += f"{(line_pfx, child_entry.Name, line_sfx)}\n"
            if expand:
                s += child_entry.list_children(level + 1, expand)
        return s

    def __str__(self) -> str:
        return f"{self.Name} ({self.ObjectType}, {hex(self.SiblingID)}, {hex(self.RightSiblingID)}, {hex(self.ChildID)}, {hex(self.StartingSectorLocation)}, {hex(self.StreamSize)})"


class MSCFB:
    fd: BufferedReader
    fat: FAT
    minifat: MiniFAT
    directory: Directory
    validCFB: bool
    SectorSize: int
    MiniStreamSectorLocation: int
    MiniStreamSize: int
    MinorVersion: int
    MajorVersion: int
    ByteOrder: int
    SectorShift: int
    MiniSectorShift: int
    DirectorySector: int
    FATSectors: int
    FirstDirectorySectorLocation: int
    TransactionSignatureNumber: int
    MiniStreamCutoffSize: int
    FirstMiniFATSectorLocation: int
    MiniFATSectors: int
    FirstDIFATSectorLocation: int
    DIFATSectors: int
    DIFAT: list[int]
    signature: bytes
    CLSID: bytes

    def __init__(self, cfb_file: _FilePathOrFileObject) -> None:
        """cfb_file is unicode or string filename or a file object"""

        if isinstance(cfb_file, BufferedReader):
            self.fd = cfb_file
        else:
            self.fd = open(cfb_file, 'rb')

        self.read_header(self.fd)
        if not self.validCFB:
            # DevSkim: ignore DS187371
            logging.debug(f'Skipping invalid MSG file: {cfb_file!r}')
            return
        if self.MajorVersion == 3:
            self.SectorSize = 512
        else:  # 4
            self.SectorSize = 4096

        self.fat = FAT(self)
        self.minifat = MiniFAT(self)
        self.directory = Directory(self)
        self.MiniStreamSectorLocation = self.directory.entries[0].StartingSectorLocation
        # Root directory entry
        self.MiniStreamSize = self.directory.entries[0].StreamSize
        self.minifat.get_all_mini_stream_fat_sectors()

    def read_header(self, fd: BufferedReader) -> None:

        self.validCFB = False
        fd.seek(0)
        self.signature = fd.read(8)
        if self.signature != b'\xD0\xCF\x11\xE0\xA1\xB1\x1A\xE1':
            return
        self.CLSID = fd.read(16)
        self.MinorVersion, self.MajorVersion, self.ByteOrder, self.SectorShift, self.MiniSectorShift = struct.unpack(
            'HHHHH', fd.read(10))
        if self.MajorVersion not in (3, 4):
            return
        _ = fd.read(6)
        self.DirectorySector, self.FATSectors, self.FirstDirectorySectorLocation, self.TransactionSignatureNumber = struct.unpack(
            'IIII', fd.read(16))
        self.MiniStreamCutoffSize, self.FirstMiniFATSectorLocation, self.MiniFATSectors, self.FirstDIFATSectorLocation, self.DIFATSectors = struct.unpack(
            'IIIII', fd.read(20))
        self.DIFAT = list(struct.unpack('I' * 109, fd.read(436)))
        self.validCFB = True

        if self.FirstDIFATSectorLocation != FAT.ENDOFCHAIN:
            raise PANHuntException('More than 109 DIFAT entries not supported')

    def get_sector_offset(self, sector: int) -> int:

        return (sector + 1) * self.SectorSize

    def get_sector_bytes(self, sector: int) -> bytes:

        offset: int = self.get_sector_offset(sector)
        self.fd.seek(offset)
        return self.fd.read(self.SectorSize)

    def __del__(self) -> None:
        self.fd.close()


###################################################################################################################################
#  __  __ ____         _____  ____  __ ____   ____
# |  \/  / ___|       / _ \ \/ /  \/  / ___| / ___|
# | |\/| \___ \ _____| | | \  /| |\/| \___ \| |  _
# | |  | |___) |_____| |_| /  \| |  | |___) | |_| |
# |_|  |_|____/       \___/_/\_\_|  |_|____/ \____|
#
###################################################################################################################################


class PropertyStream:

    PROPERTY_STREAM_NAME: str = '__properties_version1.0'
    TOPLEVEL_HEADER_SIZE: int = 32
    RECIP_OR_ATTACH_HEADER_SIZE: int = 8
    EMBEDDED_MSG_HEADER_SIZE: int = 24

    msmsg: 'MSMSG'
    properties: dict[int, 'PropertyEntry']
    NextRecipientID: int
    NextAttachmentID: int
    RecipientCount: int
    AttachmentCount: int

    def __init__(self, msmsg_obj: 'MSMSG', parent_dir_entry: DirectoryEntry, header_size: int) -> None:

        self.msmsg = msmsg_obj
        property_dir_entry: DirectoryEntry = parent_dir_entry.children[
            PropertyStream.PROPERTY_STREAM_NAME]
        property_bytes: bytes = property_dir_entry.get_data()
        self.properties = {}
        if property_bytes:
            if header_size >= PropertyStream.EMBEDDED_MSG_HEADER_SIZE:
                _, self.NextRecipientID, self.NextAttachmentID, self.RecipientCount, self.AttachmentCount = struct.unpack(
                    '8sIIII', property_bytes[:24])
            if (len(property_bytes) - header_size) % 16 != 0:
                raise PANHuntException(
                    'Property Stream size less header is not exactly divisible by 16')
            property_entries_count: int = int(
                (len(property_bytes) - header_size) / 16)
            for i in range(property_entries_count):
                prop_entry: PropertyEntry = PropertyEntry(
                    self.msmsg, parent_dir_entry, property_bytes[header_size + i * 16: header_size + i * 16 + 16])
                if prop_entry in self.properties.values():
                    raise PANHuntException(
                        'PropertyID already in properties dictionary')
                self.properties[prop_entry.PropertyID] = prop_entry

    def get_value(self, prop_id: int) -> 'PropertyEntry':  # type: ignore

        if prop_id in self.properties:
            return self.properties[prop_id]
        # raise IndexError('prop_id')

    def __str__(self) -> str:
        return '\n'.join([str(prop) for prop in list(self.properties.values())])


class PropertyEntry:

    SUB_PREFIX: str = '__substg1.0_'

    PropertyTag: int
    Flags: int
    PropertyID: int
    PropertyType: int
    size: int
    name: str
    value: _ValueType

    def __init__(self, msmsg: 'MSMSG', parent_dir_entry: DirectoryEntry, property_entry_bytes: bytes) -> None:
        propertyTag: int
        Flags: int
        propertyTag, Flags = struct.unpack('II', property_entry_bytes[:8])

        self.PropertyTag = propertyTag
        self.Flags = Flags
        self.PropertyID = self.PropertyTag >> 16
        self.PropertyType = self.PropertyTag & 0xFFFF
        ptype: MsgPTypeWrapper = msmsg.ptype_mapping[PTypeEnum(
            self.PropertyType)]
        if ptype.is_variable or ptype.is_multi:
            self.size = panutils.unpack_integer(
                'I', property_entry_bytes[8:12])
            stream_name: str = PropertyEntry.SUB_PREFIX + \
                panutils.to_zeropaddedhex(self.PropertyTag, 8)
            property_bytes: bytes = parent_dir_entry.children[stream_name].get_data(
            )

            if len(property_bytes) != self.size:
                if (ptype.ptype == PTypeEnum.PtypString and len(property_bytes) + 2 != self.size) or (ptype.ptype == PTypeEnum.PtypString8 and len(property_bytes) + 1 != self.size):
                    raise PANHuntException(
                        'Property Entry size and byte length mismatch')

            if ptype.is_multi and ptype.is_variable:
                if ptype.ptype == PTypeEnum.PtypMultipleBinary:
                    len_item_size: int = 8
                else:  # PtypMultipleString8 or PtypMultipleString
                    len_item_size = 4

                value_lengths: list[int] = []
                for i in range(int(len(property_bytes) / len_item_size)):
                    value_lengths.append(panutils.unpack_integer(
                        'I', property_bytes[i * len_item_size:i * len_item_size + 4]))

                property_byte_list: list[bytes] = []
                for i in range(len(value_lengths)):
                    index_stream_name: str = f"{stream_name}-{panutils.to_zeropaddedhex(i, 8)}"
                    property_byte_list.append(
                        parent_dir_entry.children[index_stream_name].get_data())

                self.value = ptype.get_value(
                    b''.join(property_byte_list))
            else:
                self.value = ptype.get_value(property_bytes)

        else:  # fixed size
            self.size = ptype.byte_count
            self.value = ptype.get_value(property_entry_bytes[8:8 + self.size])

    def __str__(self) -> str:
        return f"{hex(self.PropertyTag)}-{str(self.value)}"


class MsgPTypeWrapper:

    ptype: PTypeEnum
    byte_count: int
    is_variable: bool
    is_multi: bool

    def __init__(self, ptype: PTypeEnum, byte_count: int, is_variable: bool, is_multi: bool) -> None:

        self.ptype, self.byte_count, self.is_variable, self.is_multi = ptype, byte_count, is_variable, is_multi

    def get_value(self, payload: bytes) -> _ValueType:
        """payload is normally a string of bytes, but if multi and variable, bytes is a list of bytes"""

        if self.ptype == PTypeEnum.PtypInteger16:
            return panutils.unpack_integer('h', payload)
        if self.ptype == PTypeEnum.PtypInteger32:
            return panutils.unpack_integer('i', payload)
        if self.ptype == PTypeEnum.PtypFloating32:
            return panutils.unpack_float('f', payload)
        if self.ptype == PTypeEnum.PtypFloating64:
            return panutils.unpack_float('d', payload)
        if self.ptype == PTypeEnum.PtypCurrency:
            raise NotImplementedError('PtypCurrency')
        if self.ptype == PTypeEnum.PtypFloatingTime:
            return self.get_floating_time(payload)
        if self.ptype == PTypeEnum.PtypErrorCode:
            return panutils.unpack_integer('I', payload)
        if self.ptype == PTypeEnum.PtypBoolean:
            return panutils.unpack_integer('B', payload) != 0
        if self.ptype == PTypeEnum.PtypInteger64:
            return panutils.unpack_integer('q', payload)
        if self.ptype == PTypeEnum.PtypString:
            # Preventing the error:
            # UnicodeDecodeError: 'utf16' codec can't decode bytes in position 0 - 1:
            # illegal UTF - 16 surrogate
            try:
                return payload.decode('utf-16-le')  # unicode
            except UnicodeDecodeError:
                PANHuntException(
                    'String property not correctly utf-16-le encoded, ignoring errors')
                # unicode
                return payload.decode('utf-16-le', errors='ignore')
        if self.ptype == PTypeEnum.PtypString8:
            if payload[-1:] == b'\x00':
                return payload[:-1]
            else:
                return payload
        if self.ptype == PTypeEnum.PtypTime:
            return self.get_time(payload)
        if self.ptype == PTypeEnum.PtypGuid:
            return payload
        if self.ptype == PTypeEnum.PtypServerId:
            raise NotImplementedError('PtypServerId')
        if self.ptype == PTypeEnum.PtypRestriction:
            raise NotImplementedError('PtypRestriction')
        if self.ptype == PTypeEnum.PtypRuleAction:
            raise NotImplementedError('PtypRuleAction')
        if self.ptype == PTypeEnum.PtypBinary:
            return payload
        if self.ptype == PTypeEnum.PtypMultipleInteger16:
            return self.unpack_list_int(payload, 16)
        if self.ptype == PTypeEnum.PtypMultipleInteger32:
            return self.unpack_list_int(payload, 32)
        if self.ptype == PTypeEnum.PtypMultipleFloating32:
            return self.unpack_list_float(payload, 32)
        if self.ptype == PTypeEnum.PtypMultipleFloating64:
            return self.unpack_list_float(payload, 64)
        if self.ptype == PTypeEnum.PtypMultipleCurrency:
            raise NotImplementedError('PtypMultipleCurrency')
        if self.ptype == PTypeEnum.PtypMultipleFloatingTime:
            count: int = len(payload) // 8
            return [self.get_floating_time(payload[i * 8:(i + 1) * 8]) for i in range(count)]
        if self.ptype == PTypeEnum.PtypMultipleInteger64:
            self.unpack_list_int(payload=payload, bit_size=64)
        if self.ptype == PTypeEnum.PtypMultipleString:
            return ''.join([item_bytes.to_bytes(2, 'little').decode('utf-16-le') for item_bytes in payload])
        if self.ptype == PTypeEnum.PtypMultipleString8:
            return payload  # list
        if self.ptype == PTypeEnum.PtypMultipleTime:
            count = len(payload) // 8
            return [self.get_time(payload[i * 8:(i + 1) * 8]) for i in range(count)]
        if self.ptype == PTypeEnum.PtypMultipleGuid:
            count = len(payload) // 16
            return [payload[i * 16:(i + 1) * 16] for i in range(count)]
        if self.ptype == PTypeEnum.PtypMultipleBinary:
            return payload
        if self.ptype == PTypeEnum.PtypUnspecified:
            return payload
        if self.ptype == PTypeEnum.PtypNull:
            return None
        if self.ptype == PTypeEnum.PtypObject:
            return payload
        raise PANHuntException(f"Invalid PTypeEnum for value {self.ptype}")

    def unpack_list_int(self, payload: bytes, bit_size: Literal[16, 32, 64]) -> list[int]:
        format_dict: dict[int, str] = {
            16: 'h', 32: 'i', 64: 'q'}
        buffer_size = (bit_size // 8)
        count: int = len(payload) // buffer_size
        return [panutils.unpack_integer(
            format_dict[bit_size], payload[i * buffer_size:(i + 1) * buffer_size]) for i in range(count)]

    def unpack_list_float(self, payload: bytes, bit_size: Literal[32, 64]) -> list[float]:
        format_dict: dict[int, str] = {32: 'f', 64: 'd'}
        buffer_size = (bit_size // 8)
        count: int = len(payload) // buffer_size
        return [panutils.unpack_float(
            format_dict[bit_size], payload[i * buffer_size:(i + 1) * buffer_size]) for i in range(count)]

    def get_floating_time(self, time_bytes: bytes) -> datetime:

        return datetime(year=1899, month=12, day=30) + timedelta(days=panutils.unpack_float('d', time_bytes))

    def get_time(self, time_bytes: bytes) -> datetime:

        return datetime(year=1601, month=1, day=1) + timedelta(microseconds=panutils.unpack_integer('q', time_bytes) / 10.0)

    def get_multi_value_offsets(self, payload: bytes) -> tuple[int, list[int]]:

        ul_count: int = panutils.unpack_integer('I', payload[:4])
        rgul_data_offsets: list[int] = [panutils.unpack_integer(
            'I', payload[(i + 1) * 4:(i + 2) * 4]) for i in range(ul_count)]

        rgul_data_offsets.append(len(payload))
        return ul_count, rgul_data_offsets


class Recipient:
    RecipientType: int
    DisplayName: str
    ObjectType: int
    AddressType: str
    EmailAddress: str
    DisplayType: int

    def __init__(self, prop_stream: PropertyStream) -> None:

        self.RecipientType = panutils.as_int(prop_stream.get_value(
            PropIdEnum.PidTagRecipientType.value).value)
        self.DisplayName = panutils.as_str(prop_stream.get_value(
            PropIdEnum.PidTagDisplayName.value).value)
        self.ObjectType = panutils.as_int(prop_stream.get_value(
            PropIdEnum.PidTagObjectType.value).value)
        self.AddressType = panutils.as_str(prop_stream.get_value(
            PropIdEnum.PidTagAddressType.value).value)
        self.EmailAddress = panutils.as_str(prop_stream.get_value(
            PropIdEnum.PidTagEmailAddress.value).value)
        self.DisplayType = panutils.as_int(prop_stream.get_value(
            PropIdEnum.PidTagDisplayType.value).value)

    def __str__(self) -> str:
        return f"{self.DisplayName} ({self.EmailAddress})"


class Attachment:
    DisplayName: str
    AttachMethod: int
    AttachmentSize: int
    AttachFilename: str
    AttachLongFilename: str
    Filename: str
    BinaryData: Optional[bytes] = None
    AttachMimeTag: str
    AttachExtension: str

    def __init__(self, prop_stream: PropertyStream) -> None:

        self.DisplayName = panutils.as_str(prop_stream.get_value(
            PropIdEnum.PidTagDisplayName.value).value)
        self.AttachMethod = panutils.as_int(prop_stream.get_value(
            PropIdEnum.PidTagAttachMethod.value).value)
        self.AttachFilename = panutils.as_str(prop_stream.get_value(
            PropIdEnum.PidTagAttachFilename.value).value)
        self.AttachLongFilename = panutils.as_str(prop_stream.get_value(
            PropIdEnum.PidTagAttachLongFilename.value).value)
        if self.AttachLongFilename:
            self.Filename = self.AttachLongFilename
        else:
            self.Filename = self.AttachFilename
        if self.Filename:
            self.Filename = os.path.basename(self.Filename)
        else:
            self.Filename = f'[NoFilename_Method{self.AttachMethod}]'
        self.BinaryData = panutils.as_binary(prop_stream.get_value(
            PropIdEnum.PidTagAttachDataBinary.value).value)
        self.AttachExtension = panutils.as_str(prop_stream.get_value(
            PropIdEnum.PidTagAttachExtension.value).value)
        # If the msg file is from a draft, then
        # values below are null
        sz: Optional[PropertyEntry] = prop_stream.get_value(
            PropIdEnum.PidTagAttachmentSize.value)
        if sz:
            self.AttachmentSize = panutils.as_int(sz.value)
        amt: Optional[PropertyEntry] = prop_stream.get_value(
            PropIdEnum.PidTagAttachMimeTag.value)
        if amt:
            self.AttachMimeTag = panutils.as_str(amt.value)

    def __str__(self) -> str:
        size: int = 0
        if self.BinaryData:
            size = len(self.BinaryData)
        return f"{self.Filename} ({panutils.size_friendly(self.AttachmentSize)} \
        / {panutils.size_friendly(size)})"


class MSMSG:

    cfb: MSCFB
    validMSG: bool
    root_dir_entry: DirectoryEntry
    prop_stream: PropertyStream
    recipients: list[Recipient]
    attachments: list[Attachment]
    ptype_mapping: dict[PTypeEnum, MsgPTypeWrapper]
    Subject: str
    ClientSubmitTime: Optional[datetime]
    SentRepresentingName: str
    SenderName: str
    SenderSmtpAddress: str
    MessageDeliveryTime: datetime
    MessageFlags: int
    MessageStatus: int
    MessageSize: int
    Body: str
    TransportMessageHeaders: str
    DisplayTo: str
    XOriginatingIP: str

    def __init__(self, msg_file_path: _FilePathOrFileObject) -> None:
        """msg_file is unicode or string filename or a file object"""

        self.set_property_types()
        self.cfb = MSCFB(msg_file_path)
        self.validMSG = self.cfb.validCFB

        if self.validMSG is False:
            return

        self.root_dir_entry = self.cfb.directory.entries[0]
        self.prop_stream = PropertyStream(
            self, self.root_dir_entry, PropertyStream.TOPLEVEL_HEADER_SIZE)  # root

        self.set_common_properties()
        self.set_recipients()
        self.set_attachments()

    def set_common_properties(self) -> None:

        self.Subject = panutils.as_str(self.prop_stream.get_value(
            PropIdEnum.PidTagSubjectW.value).value)

        self.MessageFlags = panutils.as_int(self.prop_stream.get_value(
            PropIdEnum.PidTagMessageFlags.value).value)
        # self.HasAttachments  = (self.MessageFlags & Message.mfHasAttach == Message.mfHasAttach)

        self.Body = panutils.as_str(self.prop_stream.get_value(
            PropIdEnum.PidTagBody.value).value)

        self.DisplayTo = panutils.as_str(self.prop_stream.get_value(
            PropIdEnum.PidTagDisplayToW.value).value)

        # self.Read = (self.MessageFlags & Message.mfRead == Message.mfRead)
        # If the msg file is from a draft, then
        # values below are null
        cst: Optional[PropertyEntry] = self.prop_stream.get_value(
            PropIdEnum.PidTagClientSubmitTime.value)
        if cst:
            self.ClientSubmitTime = panutils.as_datetime(cst.value)

        srt: Optional[PropertyEntry] = self.prop_stream.get_value(
            PropIdEnum.PidTagSentRepresentingNameW.value)
        if srt:
            self.SentRepresentingName = panutils.as_str(srt.value)

        sn: Optional[PropertyEntry] = self.prop_stream.get_value(
            PropIdEnum.PidTagSenderName.value)
        if sn:
            self.SenderName = panutils.as_str(sn.value)

        ssa: Optional[PropertyEntry] = self.prop_stream.get_value(
            PropIdEnum.PidTagSenderSmtpAddress.value)
        if ssa:
            self.SenderSmtpAddress = panutils.as_str(ssa.value)

        mdt: Optional[PropertyEntry] = self.prop_stream.get_value(
            PropIdEnum.PidTagMessageDeliveryTime.value)
        if mdt:
            self.MessageDeliveryTime = panutils.as_datetime(mdt.value)

        ms: Optional[PropertyEntry] = self.prop_stream.get_value(
            PropIdEnum.PidTagMessageStatus.value)
        if ms:
            self.MessageStatus = panutils.as_int(ms.value)

        msz: Optional[PropertyEntry] = self.prop_stream.get_value(
            PropIdEnum.PidTagMessageSize.value)
        if msz:
            self.MessageSize = panutils.as_int(msz.value)

        tmh: Optional[PropertyEntry] = self.prop_stream.get_value(
            PropIdEnum.PidTagTransportMessageHeaders.value)
        if tmh:
            self.TransportMessageHeaders = panutils.as_str(tmh.value)

        x: Optional[PropertyEntry] = self.prop_stream.get_value(
            PropIdEnum.PidTagXOriginatingIp.value)
        if x:
            self.XOriginatingIP = panutils.as_str(x.value)  # x-originating-ip

    def set_recipients(self) -> None:

        self.recipients = []
        recipient_dir_index: int = 0
        while True:
            recipient_dir_name: str = f'__recip_version1.0_#{panutils.to_zeropaddedhex(recipient_dir_index, 8)}'
            if recipient_dir_name in list(self.root_dir_entry.children.keys()):
                recipient_dir_entry: DirectoryEntry = self.root_dir_entry.children[
                    recipient_dir_name]
                rps: PropertyStream = PropertyStream(
                    self, recipient_dir_entry, PropertyStream.RECIP_OR_ATTACH_HEADER_SIZE)
                recipient: Recipient = Recipient(rps)
                self.recipients.append(recipient)
                recipient_dir_index += 1
            else:
                break

    def set_attachments(self) -> None:
        self.attachments = []
        attachment_dir_index: int = 0
        while True:
            attachment_dir_name: str = f'__attach_version1.0_#{panutils.to_zeropaddedhex(attachment_dir_index, 8)}'
            if attachment_dir_name in list(self.root_dir_entry.children.keys()):
                attachment_dir_entry: DirectoryEntry = self.root_dir_entry.children[
                    attachment_dir_name]
                aps: PropertyStream = PropertyStream(
                    self, attachment_dir_entry, PropertyStream.RECIP_OR_ATTACH_HEADER_SIZE)
                attachment: Attachment = Attachment(aps)
                self.attachments.append(attachment)
                attachment_dir_index += 1
            else:
                break

    def set_property_types(self) -> None:

        self.ptype_mapping = {
            PTypeEnum.PtypInteger16: MsgPTypeWrapper(PTypeEnum.PtypInteger16, 2, False, False),
            PTypeEnum.PtypInteger32: MsgPTypeWrapper(PTypeEnum.PtypInteger32, 4, False, False),
            PTypeEnum.PtypFloating32: MsgPTypeWrapper(PTypeEnum.PtypFloating32, 4, False, False),
            PTypeEnum.PtypFloating64: MsgPTypeWrapper(PTypeEnum.PtypFloating64, 8, False, False),
            PTypeEnum.PtypCurrency: MsgPTypeWrapper(PTypeEnum.PtypCurrency, 8, False, False),
            PTypeEnum.PtypFloatingTime: MsgPTypeWrapper(PTypeEnum.PtypFloatingTime, 8, False, False),
            PTypeEnum.PtypErrorCode: MsgPTypeWrapper(PTypeEnum.PtypErrorCode, 4, False, False),
            PTypeEnum.PtypBoolean: MsgPTypeWrapper(PTypeEnum.PtypBoolean, 1, False, False),
            PTypeEnum.PtypInteger64: MsgPTypeWrapper(PTypeEnum.PtypInteger64, 8, False, False),
            PTypeEnum.PtypString: MsgPTypeWrapper(PTypeEnum.PtypString, 0, True, False),
            PTypeEnum.PtypString8: MsgPTypeWrapper(PTypeEnum.PtypString8, 0, True, False),
            PTypeEnum.PtypTime: MsgPTypeWrapper(PTypeEnum.PtypTime, 8, False, False),
            PTypeEnum.PtypGuid: MsgPTypeWrapper(PTypeEnum.PtypGuid, 16, False, False),
            PTypeEnum.PtypServerId: MsgPTypeWrapper(PTypeEnum.PtypServerId, 2, False, True),
            PTypeEnum.PtypRestriction: MsgPTypeWrapper(PTypeEnum.PtypRestriction, 0, True, False),
            PTypeEnum.PtypRuleAction: MsgPTypeWrapper(PTypeEnum.PtypRuleAction, 2, False, True),
            PTypeEnum.PtypBinary: MsgPTypeWrapper(PTypeEnum.PtypBinary, 2, False, True),
            PTypeEnum.PtypMultipleInteger16: MsgPTypeWrapper(PTypeEnum.PtypMultipleInteger16, 2, False, True),
            PTypeEnum.PtypMultipleInteger32: MsgPTypeWrapper(PTypeEnum.PtypMultipleInteger32, 2, False, True),
            PTypeEnum.PtypMultipleFloating32: MsgPTypeWrapper(PTypeEnum.PtypMultipleFloating32, 2, False, True),
            PTypeEnum.PtypMultipleFloating64: MsgPTypeWrapper(PTypeEnum.PtypMultipleFloating64, 2, False, True),
            PTypeEnum.PtypMultipleCurrency: MsgPTypeWrapper(PTypeEnum.PtypMultipleCurrency, 2, False, True),
            PTypeEnum.PtypMultipleFloatingTime: MsgPTypeWrapper(PTypeEnum.PtypMultipleFloatingTime, 2, False, True),
            PTypeEnum.PtypMultipleInteger64: MsgPTypeWrapper(PTypeEnum.PtypMultipleInteger64, 2, False, True),
            PTypeEnum.PtypMultipleString: MsgPTypeWrapper(PTypeEnum.PtypMultipleString, 2, True, True),
            PTypeEnum.PtypMultipleString8: MsgPTypeWrapper(PTypeEnum.PtypMultipleString8, 2, True, True),
            PTypeEnum.PtypMultipleTime: MsgPTypeWrapper(PTypeEnum.PtypMultipleTime, 2, False, True),
            PTypeEnum.PtypMultipleGuid: MsgPTypeWrapper(PTypeEnum.PtypMultipleGuid, 2, False, True),
            PTypeEnum.PtypMultipleBinary: MsgPTypeWrapper(PTypeEnum.PtypMultipleBinary, 2, False, True),
            PTypeEnum.PtypUnspecified: MsgPTypeWrapper(PTypeEnum.PtypUnspecified, 0, False, False),
            PTypeEnum.PtypNull: MsgPTypeWrapper(PTypeEnum.PtypNull, 0, False, False),
            PTypeEnum.PtypObject: MsgPTypeWrapper(
                PTypeEnum.PtypObject, 0, False, False)
        }
