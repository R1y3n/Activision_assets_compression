# Verification findings: FUN Labs `.fun` format

All work was read-only against the original extractor and archives. Generated
test data and logs live only under `/tmp/opencode/research/archive/`.

## Method

Independent parsers in `checks/` memory-map each archive, decrypt the fixed-key
signature, read the plaintext `(key, increment)` seed, decrypt the 24-byte
header, and advance by `comp_size`. No archive was fully decompressed; large
archives were verified by manifest walk plus at most eight sampled zlib
streams each.

```text
ARCHIVES = /mnt/windows/Program Files (x86)/temmpp/fdown/razor/fun/
REFERENCE = /mnt/windows/Program Files (x86)/temmpp/fdown/razor/extractor.py
```

Scripts used: `checks/probe.py`, `checks/verify.py`, `checks/payload.py`,
`checks/stats.py`, `checks/crypto_alt.py`, `checks/adler.py`,
`checks/test_module.py`.

## 1. Real archives: full manifest walk (12 archives, 3,402 entries)

| Archive | Entries | Bad signatures | Trailing bytes |
|---|---:|---:|---:|
| `patch1.fun`   |    1 | 0 | 0 |
| `fonts.fun`    |   14 | 0 | 0 |
| `script.fun`   |    8 | 0 | 0 |
| `editor.fun`   |   10 | 0 | 0 |
| `shaders.fun`  |   28 | 0 | 0 |
| `fx.fun`       |   36 | 0 | 0 |
| `map.fun`      |   28 | 0 | 0 |
| `cutscenes.fun`|   60 | 0 | 0 |
| `sound.fun`    |  781 | 0 | 0 |
| `bsp.fun`      |   26 | 0 | 0 |
| `objects.fun`  | 1599 | 0 | 0 |
| `textures.fun` |  811 | 0 | 0 |

The signature at every entry is `d9 c1 88 88` and decrypts with the fixed
`(0x89, +1)` stream to `50 4b 03 04`. Every one of the 3,402 decoded 24-byte
headers has `flag=0`, `method=8`, `mod_time=0`, `mod_date=0`, `crc32=0`,
`extra_len=2`; every raw extra field is exactly `78 9c`. Every walk ends at
exactly the file size, i.e. **no trailing bytes** in any shipped archive.

### Keystream reset proof (`checks/stats.py`)

| Decryption of filename | Successes / 3,402 |
|---|---:|
| Fresh key stream from entry seed (header, name, extra each restart) | **3,402** |
| Key stream carried over from end of 24-byte header | 85 |

Of the 85 carry-over "successes", 71 are entries where `24*inc ≡ 0 (mod 256)`
so the carried key equals the seed anyway, and 14 are coincidentally printable
garbage strings. For 3,331 entries the two streams actually differ. Exact example from
`fonts.fun` entry 0 (`key=0x89, inc=0x1b`, key after header `0x11`):

```text
raw name   : efcbd1ae864c6d290f08a6feac9a647b173201fe
fresh      : b'fonts\\Font1Large.fnt'
carried    : b'\xfe\xe7\x96\xcc\xfb\xd4\xde\xe7\xe6\x0c\xb9\xc4\xf9\xea\xef\xdd\xd6\xee\xf6\xec'
```

Alternative primitives were falsified (`checks/crypto_alt.py`): advance-before-
XOR produced 52 printable names, XOR-increment 9, constant key 11 — all
accidental. Only advance-after-XOR produced 3,402/3,402.

### Payload framing proof (`checks/payload.py`, `checks/adler.py`)

For 217 sampled entries (all 185 entries in archives smaller than 4 MB —
`patch1`, `fonts`, `script`, `editor`, `shaders`, `fx`, `map`, `cutscenes` —
plus the first 8 entries of each larger archive):

* `zlib.decompress(extra + payload)` succeeded, ended exactly at EOF of the
  stream (`eof=True`, `unused_data` empty), and returned exactly
  `uncomp_size` bytes — 217/217.
* `zlib.decompressobj(wbits=-15).decompress(payload)` returned the same plain
  bytes but always left **4 bytes** in `unused_data` (the Adler-32).
* For 185 of those streams (all entries of archives < 4 MB) the trailing four bytes equal
  `struct.pack(">I", zlib.adler32(plain))` and the entire `extra + payload`
  byte sequence equals `zlib.compress(plain, 6)` — 185/185 exact matches.
  So the observed data was produced with zlib level 6 / 32K window.
* Sample (`fonts.fun` entry 0): `extra=789c`, `comp_size=441`,
  `uncomp_size=701`, payload starts `25 d1 47 72 d5 40 10 06 ...` and ends
  `... 81 1f fd 07 96 e9 7a ba`; decompressed text begins `SFNTFont1Large`.

## 2. Synthetic round-trip (`checks/test_module.py`)

Created `/tmp/opencode/research/archive/checks/work/synthetic.fun` (1,719
bytes, 4 entries) with `create(..., seed=(0x89, 0x1b))` from:

| Name | Plain bytes |
|---|---:|
| `hello.txt` | 147 |
| `sub/nested.bin` | 300,000 |
| `empty.dat` | 0 |
| `latin1.txt` | 15 (includes `\x00`, `\xe9`, `\xff`) |

Independent raw parse confirmed for all four entries: signature
`d9 c1 88 88`, stored seed `89 1b`, decoded header
`(flag, method, time, date, crc, comp, uncomp, extra_len) =
(0, 8, 0, 0, 0, len(plain), 2)`, raw extra `78 9c`, payload exactly
`zlib.compress(plain, 6)[2:]`, complete zlib stream decodes to the original,
and no trailing bytes. `FunArchive.extract()` reproduced all four input files
byte-for-byte; `read_file()`, name/index/predicate selection, stored-method
creation, `bytes` and file-object sources, and the CLI (`list`, `list --json`,
`extract`, `pack --from --prefix`) all passed.

Cross-tool checks:

* The **original `extractor.py`** parsed the synthetic archive in `--dry-run`
  mode and logged all four stored names.
* Extracting `patch1.fun` (1 file) and `fonts.fun` (14 files) with the original
  tool and with `FunArchive.extract()` produced byte-for-byte identical output
  sets.
* Negative tests passed: strict mode rejects a corrupted signature, lenient
  mode stops and reports trailing bytes, traversal names are neutralized (or
  rejected with `sanitize=False`), and a corrupted Adler-32 raises
  `FunFormatError` unless `verify=False`.

## 3. Corrections to `extractor.py`'s documentation

1. **Extra field is plaintext, not obfuscated.** The file's docstring says the
   extra field is "decrypted the same way, restarted FRESH". In all 3,402 real
   entries the two raw extra bytes are `78 9c`. Decrypting them (as the code
   does at lines 202–210) yields random bytes. More importantly, these bytes
   are not ZIP extra metadata at all: they are the zlib header of the entry's
   compressed stream.
2. **Payload is not plain ZIP raw DEFLATE.** `comp_size` covers the raw
   DEFLATE body **plus a 4-byte Adler-32**, and the preceding two plaintext
   bytes complete a standard zlib stream. `wbits=-15` inflates correctly but
   leaves four bytes unconsumed; the docstring's "raw DEFLATE ... exactly like
   an ordinary zip entry" is incomplete.
3. **"No CRC" needs qualification.** The ZIP CRC field exists and is always
   zero, but every observed payload has a valid Adler-32 (verified on 185
   streams), so the format is not checksum-free.
4. **Trailing-byte quirk is a parser artifact, not archive data.** The code's
   `limit = full_size - 2` (from QuickBMS `FULLSIZE -= 2`) leaves two bytes
   unscanned. All 12 archives end exactly at the final payload byte: zero
   trailing bytes. Keeping the `-2` can skip a final small entry.
5. **Naming of the 24-byte block.** It is the standard 30-byte ZIP local
   header with the signature *and* the 2-byte "version needed" field removed;
   the plaintext seed occupies the version-needed position. It is not a
   complete 30-byte header plus an extra seed.
6. The rest of the existing description — fixed signature key `(0x89, +1)`,
   plaintext 2-byte seed, three independent keystream restarts, sequential
   entries with no padding, method 8 payloads — is confirmed.

## 4. Deliverables

* `funarchive.py` — stdlib-only reader/extractor/writer + CLI.
* `docs/fun_archive.md` — format specification.
* `checks/` — verification scripts and generated test data (supporting
  evidence only).
