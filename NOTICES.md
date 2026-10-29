# Third-party notices

pymagic-models is MIT licensed (see LICENSE). Its models contain code copied from two other projects, under
their own licenses, given in full below.

## httplib2 0.32 (MIT)

`http_model/` copies the parts of httplib2 its model of httplib2 builds on: its exceptions, its URI, header and
decompression helpers, its authentication classes, `Credentials`, `KeyCerts`, `ProxyInfo` and `Response`, and the
decoders of `httplib2.decode`. Changes: each class is marked as a copy of its original with `@model()`; the
WWW-Authenticate parser, which httplib2 writes with pyparsing, is rewritten in plain Python with the same grammar;
`parse_uri` and the header normalization match by hand rather than with regular expressions; `Response` and the
decoders take their dependencies from the standard library models.

```
Httplib2 Software License

Copyright (c) 2006 by Joe Gregorio

Permission is hereby granted, free of charge, to any person
obtaining a copy of this software and associated documentation
files (the "Software"), to deal in the Software without restriction,
including without limitation the rights to use, copy, modify, merge,
publish, distribute, sublicense, and/or sell copies of the Software,
and to permit persons to whom the Software is furnished to do so,
subject to the following conditions:

The above copyright notice and this permission notice shall be
included in all copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND,
EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES
OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND
NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS
BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN
ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN
CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

## CPython 3.10 (PSF License Version 2)

`stdlib_model/` copies parts of the Python standard library, from CPython 3.10.

Summary of the changes made to it:

- `http/client`: the exceptions of `http.client`, each marked as a copy with `@model()`; `_get_hostport` and
  `_validate_host` of `HTTPConnection` as functions, `_validate_host` finding control characters without a
  regular expression.
- `encodings/idna`: `ToASCII`, `ToUnicode` and `Codec` of `encodings.idna`, the labels split at their dots without
  a regular expression; `nameprep` is modelled rather than copied.
- `re`: `re.error` (from `sre_constants`), and the tokenizer and template parsing of `sre_parse`, which takes a
  pattern's group count and names rather than the pattern.
- `random`: the argument checks of `random.randrange`, which draws its number from `magic()`.
- `socket`: the `socket` class, `SocketIO`, `_GiveupOnSendfile`, `create_connection`, `create_server`,
  `has_dualstack_ipv6`, `getfqdn`, `socketpair` and `_intenum_converter` of `socket`, each class marked as a copy
  with `@model()`. The `socket` class builds on the model of `_socket.socket`, keeps its inheritable flag in the
  model rather than asking `os`, and sends files with `send()`; what `socket` does for platforms other than Linux
  is left out.
- `_socket`: the docstring of `_socket.socket`.
- `binascii`: `a2b_base64` follows `binascii_a2b_base64_impl` of `Modules/binascii.c`, written in Python.
- `http_model/` computes HMAC as `hmac.HMAC` does over a hash constructor, written as one function.

```
PYTHON SOFTWARE FOUNDATION LICENSE VERSION 2
--------------------------------------------

1. This LICENSE AGREEMENT is between the Python Software Foundation
("PSF"), and the Individual or Organization ("Licensee") accessing and
otherwise using this software ("Python") in source or binary form and
its associated documentation.

2. Subject to the terms and conditions of this License Agreement, PSF hereby
grants Licensee a nonexclusive, royalty-free, world-wide license to reproduce,
analyze, test, perform and/or display publicly, prepare derivative works,
distribute, and otherwise use Python alone or in any derivative version,
provided, however, that PSF's License Agreement and PSF's notice of copyright,
i.e., "Copyright (c) 2001, 2002, 2003, 2004, 2005, 2006, 2007, 2008, 2009, 2010,
2011, 2012, 2013, 2014, 2015, 2016, 2017, 2018, 2019, 2020, 2021, 2022, 2023 Python Software Foundation;
All Rights Reserved" are retained in Python alone or in any derivative version
prepared by Licensee.

3. In the event Licensee prepares a derivative work that is based on
or incorporates Python or any part thereof, and wants to make
the derivative work available to others as provided herein, then
Licensee hereby agrees to include in any such work a brief summary of
the changes made to Python.

4. PSF is making Python available to Licensee on an "AS IS"
basis.  PSF MAKES NO REPRESENTATIONS OR WARRANTIES, EXPRESS OR
IMPLIED.  BY WAY OF EXAMPLE, BUT NOT LIMITATION, PSF MAKES NO AND
DISCLAIMS ANY REPRESENTATION OR WARRANTY OF MERCHANTABILITY OR FITNESS
FOR ANY PARTICULAR PURPOSE OR THAT THE USE OF PYTHON WILL NOT
INFRINGE ANY THIRD PARTY RIGHTS.

5. PSF SHALL NOT BE LIABLE TO LICENSEE OR ANY OTHER USERS OF PYTHON
FOR ANY INCIDENTAL, SPECIAL, OR CONSEQUENTIAL DAMAGES OR LOSS AS
A RESULT OF MODIFYING, DISTRIBUTING, OR OTHERWISE USING PYTHON,
OR ANY DERIVATIVE THEREOF, EVEN IF ADVISED OF THE POSSIBILITY THEREOF.

6. This License Agreement will automatically terminate upon a material
breach of its terms and conditions.

7. Nothing in this License Agreement shall be deemed to create any
relationship of agency, partnership, or joint venture between PSF and
Licensee.  This License Agreement does not grant permission to use PSF
trademarks or trade name in a trademark sense to endorse or promote
products or services of Licensee, or any third party.

8. By copying, installing or otherwise using Python, Licensee
agrees to be bound by the terms and conditions of this License
Agreement.

```
