# FUN Labs `.fun` archive format

Verified against 12 shipped archives (3,402 entries) and a synthetic round-trip.
This document describes the format actually observed in those files, including
places where the reference `extractor.py` description is misleading.

## 1. Container layout

A `.fun` file is a **flat sequence of entries** starting at byte 0. There is no
ZIP central directory, no end-of-central-directory record, and no padding: the
next entry starts immediately after the previous entry's payload.

All offsets below are relative to the start of one entry.

| Offset | Size | Field | Encoding |
|---|---:|---|---|
| `+0`  | 4 | ZIP local signature | XOR-obfuscated with a **fixed** key (see §2) |
| `+4`  | 2 | Per-entry seed `(key, increment)` | **plaintext** |
| `+6`  | 24 | Mini local header | obfuscated, fresh keystream from seed |
| `+30` | `name_len` | Filename | obfuscated, fresh keystream from seed |
| `+30+name_len` | `extra_len` | Extra field | **plaintext** in all observed files |
| `+30+name_len+extra_len` | `comp_size` | Payload | not obfuscated |

Entry span = `30 + name_len + extra_len + comp_size`.

The 24-byte mini header is the standard PKZIP local file header with the
4-byte signature **and** the 2-byte "version needed to extract" field removed.
The plaintext seed occupies the version-needed slot. Decoded layout
(`struct "<4H3I2H"`, little-endian):

| Bytes | Type | Field |
|---:|---|---|
| 0–1   | `u16` | general purpose bit flag |
| 2–3   | `u16` | compression method (`8` = DEFLATE) |
| 4–5   | `u16` | DOS modification time |
| 6–7   | `u16` | DOS modification date |
| 8–11  | `u32` | CRC-32 (zero in every shipped entry) |
| 12–15 | `u32` | `comp_size` |
| 16–19 | `u32` | `uncomp_size` |
| 20–21 | `u16` | `name_len` |
| 22–23 | `u16` | `extra_len` |

All 3,402 observed entries have `flag=0`, `method=8`, `time=0`, `date=0`,
`crc32=0`, `extra_len=2`. Names are backslash-separated Latin-1 (in practice
ASCII) paths such as `script\Game\gamedata.lua`; there are no directory
entries in the observed archives.

## 2. Crypto primitive

The same symmetric transform is used for every obfuscated field:

```python
k = key & 0xFF
for i, byte in enumerate(plain_or_cipher):
    out[i] = byte ^ k
    k = (k + inc) & 0xFF       # additive, modulo 256, AFTER the XOR
```

Encryption and decryption are identical. Key and increment come from two
distinct sources:

1. **Signature:** always `key=0x89`, `inc=0x01`, independent of the entry.
   The four encrypted bytes are always `d9 c1 88 88` and decode to
   `50 4b 03 04` (`PK\x03\x04`).
2. **Everything else in an entry:** `key = raw[+4]`, `inc = raw[+5]`, read
   verbatim (they are not encrypted).

### Per-field keystream reset

The keystream is restarted from `(key, inc)` **three times per entry**:

* once for the 24-byte mini header,
* again at byte 0 of the filename,
* again at byte 0 of the extra field (if non-empty).

It never carries over between fields. In particular, the key value left after
the header, `(key + 24*inc) & 0xFF`, is *not* the starting key for the name.
This matters: when `inc` is not a multiple of 32 the two streams differ, and
carrying the key over yields garbage. In the sample set, restarting decodes
3,402/3,402 names correctly; carrying over decodes only 85, of which 71 are
cases where the 24-byte header happens to restore the original key
(`24*inc ≡ 0 mod 256`) and 14 are coincidentally printable garbage.

Extra bytes are stored **plaintext**. In every observed entry the two bytes
are `78 9c`, a standard zlib header.

## 3. Payload decoding

For the observed `method=8` entries, the payload is a zlib stream split across
two adjacent fields:

```
extra (2 bytes, plaintext) = 78 9c        # CMF/FLG: CM=8, 32K window, default level
payload (comp_size bytes) = raw DEFLATE body + 4-byte big-endian Adler-32
```

Equivalently:

```python
compressed_stream = extra + payload       # a complete, ordinary zlib stream
plain = zlib.decompress(compressed_stream)
len(plain) == uncomp_size
```

`comp_size` therefore **includes the 4-byte Adler-32 trailer** but excludes
the 2-byte zlib header. `crc32` in the mini header is always zero; integrity is
provided by that Adler-32 instead ("no CRC" refers only to the ZIP field).

A parser may also call `zlib.decompressobj(wbits=-15)` on `payload` alone; that
correctly inflates the data but leaves the final 4 bytes in `unused_data`.
Those 4 bytes must not be mistaken for padding or for the next entry's header.

`method=0` (stored) was never seen in shipped data; the reference tool treats
the payload as already-plain bytes. The module in this directory can write
stored entries as a synthetic extension (`extra_len=0`, payload = plain bytes).

## 4. Worked example: first entry of `fonts.fun`

```
raw signature     : d9 c1 88 88  ->  rolling_xor(..., 0x89, 1)  ->  PK\x03\x04
raw seed bytes    : 89 1b        ->  key=0x89, inc=0x1b
raw 24B header    : 89 a4 b7 da f5 10 2b 46 61 7c 97 b2
                    74 e9 03 1e 84 56 6f 8a b1 c0 d9 f6
decoded header    : 00 00 08 00 00 00 00 00 00 00 00 00
                    b9 01 00 00 bd 02 00 00 14 00 02 00
                  => method=8, crc=0, comp=441, uncomp=701, name_len=20, extra_len=2
raw name          : ef cb d1 ae 86 4c 6d 29 0f 08 a6 fe ac 9a 64 7b 17 32 01 fe
fresh key stream  : "fonts\\Font1Large.fnt"
continued stream  : fe e7 96 cc fb d4 de ... (garbage; k_after_header = 0x11)
raw extra         : 78 9c        (plaintext zlib header, NOT decrypted)
payload first 16  : 25 d1 47 72 d5 40 10 06 e0 51 18 79 9e 9f a2 85
payload last 8    : 81 1f fd 07 96 e9 7a ba   (last 4 = Adler-32)
```

`78 9c + 441 payload bytes` inflate to exactly 701 bytes beginning
`SFNTFont1Large`. Across the smaller archives, all sampled streams are
byte-for-byte identical to `zlib.compress(plain, 6)[2:]`, and the final four
payload bytes equal `zlib.adler32(plain)` big-endian.

## 5. Quirks and pitfalls

* **Fixed signature key.** Do not try to derive the signature key from the
  per-entry seed; it is always `(0x89, 1)`.
* **Two-byte seed is plaintext,** and only two bytes. Every field restarts
  from it; do not chain fields.
* **24-byte header, not 30.** Account for `4 + 2 + 24` before the filename.
* **"Extra field" is a zlib header.** It is not ZIP extra data and is not
  encrypted. Decrypting it (as the reference extractor does) is harmless only
  because the value is never used.
* **No usable ZIP CRC.** The header CRC is zero; use the Adler-32 at the end
  of the payload, or simply validate the complete zlib stream.
* **Trailing bytes.** QuickBMS-derived readers subtract 2 from the file size
  (`FULLSIZE -= 2`), leaving the last two bytes unscanned. All 12 archives
  examined end exactly at the last payload byte: **zero trailing bytes**.
  Do not emulate the `-2`; it can skip a final entry. This reader records a
  `trailing_bytes` count instead of assuming one.
* **Sequential walk.** Advancing by `comp_size` is mandatory; there is no
  central directory to fall back on if a size is wrong.
* **Names** use backslashes and Latin-1 bytes. Sanitize before writing to a
  host filesystem (the provided module does this by default).

## 6. Writing archives

`funarchive.create()` / `funarchive.write()` emit exactly this layout:

1. fixed-key signature;
2. two plaintext seed bytes;
3. header + filename obfuscated with fresh keystreams from that seed;
4. `extra = zlib.compressobj(level, DEFLATED, 15)` header bytes (default
   `78 9c`) written **plaintext**;
5. raw DEFLATE body from `compressobj(level, DEFLATED, -15)` followed by
   `struct.pack(">I", zlib.adler32(plain))`.

The resulting bytes are accepted by the original reference extractor and round
trip through this module. See `FINDINGS.md` for the test evidence.
