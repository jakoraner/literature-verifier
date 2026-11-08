# Library links for `litverify obtain`

`litverify obtain` adds a "via your library" link to every source you still need. The
link comes from a template you set once in `litverify.toml`:

```toml
[obtain]
library_link = "https://YOUR-PROXY.example.edu/login?url={doi_url}"
```

Placeholders (all URL-encoded by the tool):

| Placeholder | Value |
|---|---|
| `{doi}` | the DOI, e.g. `10.1038/sdata.2016.18` |
| `{doi_url}` | `https://doi.org/<doi>`, encoded for use inside another URL |
| `{title}` | the title from the bib entry |
| `{query}` | the DOI if there is one, else the title |

A template that uses only `{doi}`/`{doi_url}` gives no link for entries without a DOI;
use `{query}` or `{title}` for a search-based template that covers those too.

The host names below are placeholders. Find your own by opening any paper through your
library's website and looking at the address bar, or ask your library which of these
systems it uses. Test the template on one DOI before relying on it.

## OpenURL link resolver ("Find it", "Get it", SFX, Alma uresolver)

Most university libraries run an OpenURL resolver behind the "Find it @ library" buttons.
It takes a DOI and shows every route to the full text you are entitled to.

```toml
library_link = "https://RESOLVER.example.edu/openurl?ctx_ver=Z39.88-2004&rft_id=info:doi/{doi}"
```

Ex Libris Alma libraries usually have a resolver of the form
`https://INSTITUTION.alma.exlibrisgroup.com/view/uresolver/INST_CODE/openurl?...`.

## EZproxy

EZproxy rewrites a publisher URL so that it goes through your institution's login:

```toml
library_link = "https://PROXY.example.edu/login?url={doi_url}"
```

## OpenAthens redirector

```toml
library_link = "https://go.openathens.net/redirector/YOUR-DOMAIN.example.edu?url={doi_url}"
```

## Discovery search (Primo, Summon, EDS, a library catalogue)

Search links work for books and for entries without a DOI. Copy the URL of a search
results page in your library's discovery system and replace the search terms with
`{query}`, for example for Primo VE:

```toml
library_link = "https://SEARCH.example.edu/discovery/search?vid=INST:VIEW&query=any,contains,{query}"
```

## Google Scholar (any institution)

If you have set your library under Scholar's "Library links" setting, Scholar search
results carry your library's full-text links:

```toml
library_link = "https://scholar.google.com/scholar?q={query}"
```

## Open-access lookup

Separately from the library link, `obtain` can ask Unpaywall for a legal free copy. The
Unpaywall API requires a contact e-mail; set your own, and only if you are happy for it to
be sent to Unpaywall with each lookup:

```toml
unpaywall_email = "you@example.edu"
```

Leave it empty to switch the lookup off. The tool never supplies an address of its own,
and the address is redacted from the on-disk cache.
