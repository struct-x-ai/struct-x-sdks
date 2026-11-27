package structx

// Version is the semantic version of this SDK. It is surfaced in the
// User-Agent header so the backend can attribute requests during ops
// debugging without leaking customer identity.
//
// Its API surface matches the Python (structx-sdk) and Node
// (@struct-x/sdk) SDKs at their 0.5.x line — the full extract / document /
// infer / template / personal-schema / synth / canonical surface — on its own
// v0.x line (which began at 0.1.0, same as how both sibling SDKs began).
//
// 0.1.1 aligns with the sibling SDKs' 0.4.1 bug-fix release: the extract
// response id decodes from the wire field `id`, Usage hits /billing/usage,
// and an optional bearer-token credential unlocks the JWT-only methods.
//
// 0.2.0 aligns with the sibling SDKs' 0.5.0 release: ExtractFile reads PDFs
// through POST /v1/extract/document, and the default base URL is
// https://api.struct-x.ai.
const Version = "0.2.0"
