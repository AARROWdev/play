' PLAY for Roku.
' Finds the PLAY program on the network, then: HOME / KIDS -> MOVIES / SHOWS -> (collection | show -> season) -> play.
' One list node shows every level; m.level says which one, m.items holds what each row stands for.
sub init()
    m.menu = m.top.findNode("menu")
    m.list = m.top.findNode("list")
    m.video = m.top.findNode("video")
    m.status = m.top.findNode("status")
    m.header = m.top.findNode("header")
    m.hint = m.top.findNode("hint")
    m.bar = m.top.findNode("searchBar")
    m.barBg = m.top.findNode("searchBg")
    m.barText = m.top.findNode("searchText")
    m.typeMenu = m.top.findNode("typeMenu")
    m.menu.observeField("buttonSelected", "onMenu")
    m.typeMenu.observeField("buttonSelected", "onType")
    m.list.observeField("itemSelected", "onSelect")
    m.video.observeField("state", "onState")
    m.video.observeField("position", "onPosition")
    m.reg = CreateObject("roRegistrySection", "play")
    m.resume = CreateObject("roRegistrySection", "resume")
    m.libs = ["HOME", "KIDS"]
    m.accent = "0xF2C14EFF"
    m.query = ""
    m.items = []
    m.seekTo = 0
    m.focusBefore = ""
    m.dialogHandled = false
    m.server = ""
    if m.reg.Exists("server") then m.server = m.reg.Read("server")
    if m.server = "" then discover() else showMenu()
end sub

' ---------------------------------------------------------------- finding the PC
sub discover()
    m.level = "find"
    hideAll()
    m.header.text = "PLAY"
    m.status.text = "Looking for PLAY on your network…"
    m.hint.text = ""
    m.disc = CreateObject("roSGNode", "DiscoverTask")
    m.disc.observeField("result", "onDiscovered")
    m.disc.control = "RUN"
end sub

sub onDiscovered()
    found = ParseJson(m.disc.result)
    if found = invalid then found = []
    if found.Count() = 1
        useServer(found[0].url)
        return
    end if
    titles = []
    for each f in found
        titles.Push(f.pc + "   (" + f.url + ")")
    end for
    titles.Push("Enter the PC's address myself")
    found.Push(invalid)
    st = "Pick the PC running PLAY."
    if found.Count() = 1
        st = "PLAY wasn't found. Is the PLAY program running on your PC, on the same network as this TV? If Windows calls that network Public, set it to Private. " + "Or type the PC's address on the last line."
        titles = ["Search again", "Enter the PC's address myself"]
        found = ["again", invalid]
    end if
    m.lib = "PC"
    setList("pickpc", "PLAY", titles, found, st)
    m.bar.visible = false
    m.hint.text = ""
end sub

sub useServer(url as string)
    m.server = url
    m.reg.Write("server", url)
    m.reg.Flush()
    showMenu()
end sub

sub askAddress()
    kb = CreateObject("roSGNode", "KeyboardDialog")
    kb.title = "The PC's address"
    kb.message = ["For example 192.168.1.20. It is shown at the bottom of PLAY's window on the PC."]
    kb.buttons = ["OK", "Cancel"]
    kb.text = ""
    kb.observeField("buttonSelected", "onAddress")
    m.addrKb = kb
    openDialog(kb)
end sub

sub onAddress()
    t = m.addrKb.text.Trim()
    pressed = m.addrKb.buttonSelected
    m.dialogHandled = true
    m.addrKb.close = true
    url = ""
    if pressed = 0 then url = serverUrl(t)
    if url = ""
        if pressed = 0 then m.status.text = "That isn't an address PLAY can use. Try it like 192.168.1.20."
        restoreFocus()
        return
    end if
    useServer(url)
end sub

' What was typed -> "http://host:port", or "" if it isn't an address. Accepts "192.168.1.20" (port 8600),
' "192.168.1.20:8700", "192.168.1.20 8700", "http://192.168.1.20/", "https://...", "[fe80::1]:8600".
function serverUrl(t as string) as string
    t = t.Trim()
    low = LCase(t)
    if Left(low, 7) = "http://"
        t = Mid(t, 8)
    else if Left(low, 8) = "https://"
        t = Mid(t, 9)
    end if
    for each stopper in ["/", "?", "#"]
        cut = Instr(1, t, stopper)
        if cut > 0 then t = Left(t, cut - 1)
    end for
    t = t.Trim()
    sp = Instr(1, t, " ")
    if sp > 0 then t = Left(t, sp - 1) + ":" + Mid(t, sp + 1).Trim()
    host = t
    port = ""
    if Left(t, 1) = "["                          ' [IPv6]:port
        close = Instr(1, t, "]")
        if close = 0 then return ""
        host = Left(t, close)
        rest = Mid(t, close + 1)
        if Left(rest, 1) = ":" then port = Mid(rest, 2)
    else
        colon = Instr(1, t, ":")
        if colon > 0
            host = Left(t, colon - 1)
            port = Mid(t, colon + 1)
        end if
    end if
    if host = "" then return ""
    if port = "" then port = "8600"
    n = port.ToInt()
    if n < 1 or n > 65535 or n.ToStr() <> port then return ""
    return "http://" + host + ":" + port
end function

' Dialogs remember what had focus; Back on a keyboard or dialog (no button) gives it back. A button's handler
' moves focus itself, so wasClosed after a button press does nothing.
sub openDialog(d as object)
    if m.bar.hasFocus()
        m.focusBefore = "bar"
    else if m.list.hasFocus()
        m.focusBefore = "list"
    else
        m.focusBefore = ""
    end if
    m.dialogHandled = false
    d.observeField("wasClosed", "onDialogClosed")
    m.top.dialog = d
end sub

sub onDialogClosed()
    if m.dialogHandled then return
    restoreFocus()
end sub

sub restoreFocus()
    if m.video.visible then return
    if m.focusBefore = "bar" and m.bar.visible
        focusBar()
    else if m.focusBefore = "list" and m.list.visible and m.items.Count() > 0
        focusList()
    else if m.level = "menu"
        m.menu.setFocus(true)
    else if m.level = "type"
        m.typeMenu.setFocus(true)
    else if m.list.visible and m.items.Count() > 0
        focusList()
    else if m.bar.visible
        focusBar()
    else
        m.top.setFocus(true)
    end if
end sub

function plural(n as integer, word as string) as string
    if n = 1 then return "1 " + word
    return n.ToStr() + " " + word + "s"
end function

' ---------------------------------------------------------------- the two button screens
sub hideAll()
    m.list.visible = false
    m.bar.visible = false
    m.typeMenu.visible = false
    m.menu.visible = false
end sub

sub showMenu()
    m.level = "menu"
    hideAll()
    m.menu.visible = true
    m.header.text = "PLAY"
    m.status.text = ""
    m.hint.text = "*  change PC"
    m.lib = invalid
    m.query = ""
    m.menu.setFocus(true)
end sub

sub showTypeMenu()
    m.level = "type"
    hideAll()
    m.typeMenu.visible = true
    m.header.text = m.lib
    m.status.text = plural(m.movieCount, "movie") + ", " + plural(m.shows.Count(), "show")
    m.hint.text = ""
    m.typeMenu.setFocus(true)
end sub

sub onType()
    m.typeMenu.visible = false
    if m.typeMenu.buttonSelected = 0 then showMovies() else showShows()
end sub

sub onMenu()
    m.lib = m.libs[m.menu.buttonSelected]
    m.menu.visible = false
    m.query = ""
    load()
end sub

' ---------------------------------------------------------------- loading
sub load()
    m.level = "loading"
    m.header.text = m.lib
    m.status.text = "Loading…"
    m.hint.text = ""
    hideAll()
    if m.task <> invalid then m.task.unobserveField("result")   ' an earlier, abandoned load must not answer
    m.task = CreateObject("roSGNode", "LoadTask")
    m.task.url = m.server + "/api/tv?side=" + LCase(m.lib)
    m.task.observeField("result", "onLoaded")
    m.task.control = "RUN"
end sub

sub onLoaded()
    if m.level <> "loading" then return        ' Back was pressed while it loaded
    r = m.task.result
    v = invalid
    if Left(r, 6) <> "ERROR:" then v = ParseJson(r)
    if v = invalid or type(v) <> "roAssociativeArray" or v.movies = invalid
        if r = "ERROR:503"                    ' PLAY is running, reading its folders for the first time
            m.status.text = "PLAY is reading your folders on the PC…"
            m.retry = CreateObject("roSGNode", "Timer")
            m.retry.duration = 3
            m.retry.observeField("fire", "load")
            m.retry.control = "start"
            return
        end if
        m.level = "offline"
        if r = "ERROR:403"
            m.status.text = "PLAY refused the address " + m.server + ". Type the PC's IP address instead (* then the last line)."
        else if r = "ERROR:0"
            m.status.text = "Can't reach PLAY at " + m.server + ". Is the PC on, with the PLAY program running?"
        else
            m.status.text = "PLAY at " + m.server + " answered with an error (" + Mid(r, 7) + "). Try again in a moment."
        end if
        m.hint.text = "OK  try again       *  find the PC again"
        m.top.setFocus(true)
        return
    end if
    if v.accent <> invalid and Len(v.accent) = 7 then setAccent("0x" + UCase(Mid(v.accent, 2)) + "FF")
    m.movies = v.movies
    m.shows = v.shows
    m.all = []          ' everything playable, for search
    m.movieCount = 0
    for each mv in m.movies
        if mv.kind = "collection"
            for each c in mv.items
                m.all.Push(c)
                m.movieCount = m.movieCount + 1
            end for
        else
            m.all.Push(mv)
            m.movieCount = m.movieCount + 1
        end if
    end for
    for each s in m.shows
        for each se in s.seasons
            for each e in se.episodes
                m.all.Push(e)
            end for
        end for
    end for
    if m.shows.Count() > 0 then showTypeMenu() else showMovies()   ' no shows on this side: straight to the movies
end sub

sub setAccent(c as string)
    m.accent = c
    m.list.focusBitmapBlendColor = c
end sub

' ---------------------------------------------------------------- list levels
sub setList(level as string, header as string, titles as object, items as object, status as string)
    m.level = level
    m.header.text = header
    m.items = items
    content = CreateObject("roSGNode", "ContentNode")
    for each t in titles
        c = content.createChild("ContentNode")
        c.title = t
    end for
    m.list.content = content
    m.list.jumpToItem = 0
    m.menu.visible = false
    m.typeMenu.visible = false
    m.list.visible = true
    m.bar.visible = true
    if m.query <> "" then m.barText.text = "Search:  " + m.query else m.barText.text = "Search " + m.lib + "..."
    m.status.text = status
    m.hint.text = ""
    if items.Count() = 0 then focusBar() else focusList()
end sub

function label(it as object) as string
    t = it.title
    if it.kind = "movie" and it.year <> invalid then t = t + "   (" + it.year.ToStr() + ")"
    spot = resumeAt(it.url)
    if spot > 0 then t = t + "   ·  resume at " + fmtTime(spot)
    return t
end function

' Resume points are stored as "seconds|when saved" (older builds: just seconds).
function resumeAt(url as string) as integer
    if not m.resume.Exists(url) then return 0
    return m.resume.Read(url).Split("|")[0].ToInt()
end function

sub showMovies()
    titles = []
    for each mv in m.movies
        if mv.kind = "collection"
            titles.Push(mv.title + "   ·  " + plural(mv.items.Count(), "film"))
        else
            titles.Push(label(mv))
        end if
    end for
    st = plural(m.movieCount, "movie")
    if m.movies.Count() = 0 then st = "No movies here yet. Add some in PLAY on the PC."
    setList("movies", m.lib + "  ›  MOVIES", titles, m.movies, st)
end sub

sub showCollection(c as object)
    m.col = c
    titles = []
    for each mv in c.items
        titles.Push(label(mv))
    end for
    setList("collection", m.lib + "  ›  " + c.title, titles, c.items, plural(c.items.Count(), "film") + ", in order")
end sub

sub showShows()
    titles = []
    for each s in m.shows
        titles.Push(s.name)
    end for
    setList("shows", m.lib + "  ›  SHOWS", titles, m.shows, plural(m.shows.Count(), "show"))
end sub

sub showSeasons(show as object)
    m.show = show
    titles = []
    for each se in show.seasons
        name = "Season " + se.season.ToStr()
        if se.season = 0 then name = "Specials"
        titles.Push(name + "   (" + plural(se.episodes.Count(), "episode") + ")")
    end for
    setList("seasons", m.lib + "  ›  SHOWS  ›  " + show.name, titles, show.seasons, plural(show.seasons.Count(), "season"))
end sub

sub showEpisodes(season as object)
    m.season = season
    titles = []
    for each e in season.episodes
        titles.Push(label(e))
    end for
    sname = "Season " + season.season.ToStr()
    if season.season = 0 then sname = "Specials"
    setList("episodes", m.lib + "  ›  " + m.show.name + "  ›  " + sname, titles, season.episodes, plural(season.episodes.Count(), "episode"))
end sub

' Search: everything on this side (movies, films inside collections, episodes), from the bar above any list.
sub showSearch()
    q = LCase(m.query)
    hits = []
    titles = []
    for each it in m.all
        if Instr(1, LCase(it.title), q) > 0
            hits.Push(it)
            titles.Push(label(it))
        end if
    end for
    st = plural(hits.Count(), "result") + " for " + Chr(34) + m.query + Chr(34)
    if hits.Count() = 0 then st = "No matches for " + Chr(34) + m.query + Chr(34) + ". Back returns."
    setList("search", m.lib + "  ›  SEARCH", titles, hits, st)
end sub

sub onSelect()
    i = m.list.itemSelected
    it = m.items[i]
    if m.level = "pickpc"
        if it = invalid
            askAddress()
        else if type(it) = "roString" or type(it) = "String"
            discover()
        else
            useServer(it.url)
        end if
    else if m.level = "shows"
        showSeasons(it)
    else if m.level = "seasons"
        showEpisodes(it)
    else if m.level = "movies" and it.kind = "collection"
        showCollection(it)
    else
        pick(it)    ' movies, collection films, episodes, search results: play
    end if
end sub

sub openSearch(scope as string)
    kb = CreateObject("roSGNode", "KeyboardDialog")
    kb.title = "Search " + scope
    kb.buttons = ["Search", "Cancel"]
    kb.text = m.query
    kb.observeField("buttonSelected", "onSearchButton")
    m.kb = kb
    openDialog(kb)
end sub

sub onSearchButton()
    q = m.kb.text.Trim()
    pressed = m.kb.buttonSelected
    m.dialogHandled = true
    m.kb.close = true
    if pressed = 0 and q <> ""
        if m.level <> "search" then m.beforeSearch = m.level
        m.query = q
        showSearch()
    else
        focusBar()
    end if
end sub

sub focusBar()
    m.barBg.color = m.accent
    m.barText.color = "0x101014FF"
    m.bar.setFocus(true)
end sub

sub focusList()
    m.barBg.color = "0x24242EFF"
    if m.query <> "" then m.barText.color = "0xFFFFFFFF" else m.barText.color = "0x9A9AA8FF"
    m.list.setFocus(true)
end sub

' ---------------------------------------------------------------- playback + resume (registry "resume", key = file url)
sub pick(mv as object)
    m.current = mv
    spot = 0
    spot = resumeAt(mv.url)
    if spot >= 30
        d = CreateObject("roSGNode", "Dialog")
        d.title = mv.title
        d.buttons = ["Resume from " + fmtTime(spot), "Start over"]
        d.observeField("buttonSelected", "onResumeButton")
        m.resumeDlg = d
        m.resumePos = spot
        openDialog(d)
    else
        play(mv, 0)
    end if
end sub

sub onResumeButton()
    b = m.resumeDlg.buttonSelected
    m.dialogHandled = true
    m.resumeDlg.close = true
    if b = 0 then play(m.current, m.resumePos) else play(m.current, 0)
end sub

sub play(mv as object, start as integer)
    m.current = mv
    c = CreateObject("roSGNode", "ContentNode")
    c.url = m.server + mv.url
    fmt = mv.format
    if mv.stream <> invalid then fmt = mv.stream   ' the PC says how to play it (a .mov plays as mp4)
    if fmt = "mov" then fmt = "mp4"
    c.streamFormat = fmt
    c.title = mv.title
    m.video.content = c
    m.video.notificationInterval = 1
    m.seekTo = start              ' seek once playing, rather than PlayStart, so rewinding before it still works
    m.lastSave = start            ' the spot just read isn't written straight back
    m.video.visible = true
    m.video.control = "play"
    m.video.setFocus(true)
end sub

' Resume points for files that were deleted or replaced are never asked for again; keep at most 300 so the
' channel's registry space (shared with the PC's address) never fills up.
sub trimResume()
    keys = m.resume.GetKeyList()
    if keys.Count() < 300 then return
    ages = []                                  ' drop the 50 saved longest ago (no time stored: oldest)
    for each k in keys
        parts = m.resume.Read(k).Split("|")
        t = 0
        if parts.Count() > 1 then t = parts[1].ToInt()
        ages.Push({key: k, t: t})
    end for
    ages.SortBy("t")
    for i = 0 to 49
        m.resume.Delete(ages[i].key)
    end for
end sub

function fmtTime(sec as integer) as string
    h = Int(sec / 3600)
    mi = Int((sec mod 3600) / 60)
    se = sec mod 60
    mm = mi.ToStr()
    if mi < 10 and h > 0 then mm = "0" + mm
    ss = se.ToStr()
    if se < 10 then ss = "0" + ss
    if h > 0 then return h.ToStr() + ":" + mm + ":" + ss
    return mm + ":" + ss
end function

' Every 30 s of playback the spot is saved, so Home or switching the TV off still resumes about where it stopped.
' While playing, the spot is saved every 30 s, so Home or switching the TV off still resumes about where it stopped.
sub onPosition()
    if m.current = invalid or m.video.state <> "playing" then return
    p = Int(m.video.position)
    if p - m.lastSave >= 30 or m.lastSave - p >= 30
        m.lastSave = p
        savePos(false)
    end if
end sub

' keepOnly: only ever write a spot (on an error, a position near 0 must not wipe a good resume point).
sub savePos(keepOnly = false as boolean)
    if m.current = invalid then return
    url = m.current.url
    spot = Int(m.video.position)
    dur = m.video.duration
    if spot < 30 or (dur > 0 and spot > dur - 120)
        if keepOnly or not m.resume.Exists(url) then return
        m.resume.Delete(url)
    else
        if not m.resume.Exists(url) then trimResume()
        if not m.resume.Write(url, spot.ToStr() + "|" + CreateObject("roDateTime").AsSeconds().ToStr())
            print "resume point not saved"
        end if
    end if
    m.resume.Flush()
end sub

sub onState()
    s = m.video.state
    if s = "playing" and m.seekTo > 0
        m.video.seek = m.seekTo
        m.seekTo = 0
    else if s = "error"
        savePos(true)
        msg = "This file can't play on this TV (error " + m.video.errorCode.ToStr() + ": " + m.video.errorMsg + "). The PLAY window on the PC lists files that need a look."
        print "PLAYBACK ERROR "; m.video.errorCode; " "; m.video.errorMsg; " | "; m.video.errorStr
        closeVideo(false)
        m.status.text = msg        ' after closeVideo: redrawing the list would overwrite it
    else if s = "paused"
        savePos()
    else if s = "finished"
        if m.current <> invalid
            m.resume.Delete(m.current.url)
            m.resume.Flush()
        end if
        closeVideo(false)
    end if
end sub

sub closeVideo(save as boolean)
    if save then savePos(false)
    m.current = invalid            ' before stop: the position dropping to 0 must not reach onPosition as this title
    m.video.control = "stop"
    m.video.visible = false
    at = m.list.itemFocused        ' refresh the resume marks, stay on the same row
    redraw(m.level)
    m.list.jumpToItem = at
end sub

sub redraw(lv as string)
    if lv = "episodes"
        showEpisodes(m.season)
    else if lv = "seasons"
        showSeasons(m.show)
    else if lv = "shows"
        showShows()
    else if lv = "collection"
        showCollection(m.col)
    else if lv = "search"
        showSearch()
    else
        showMovies()
    end if
end sub

' Back walks up one level: episodes -> seasons -> shows -> MOVIES/SHOWS -> HOME/KIDS; collection -> movies.
sub goBack()
    if m.level = "search"
        m.query = ""
        redraw(m.beforeSearch)
    else if m.level = "episodes"
        showSeasons(m.show)
    else if m.level = "seasons"
        showShows()
    else if m.level = "collection"
        showMovies()
    else if (m.level = "shows" or m.level = "movies") and m.shows <> invalid and m.shows.Count() > 0
        showTypeMenu()
    else if m.level = "pickpc"
        discover()
    else
        showMenu()
    end if
end sub

function onKeyEvent(key as string, press as boolean) as boolean
    if not press then return false
    if key = "back"
        if m.video.visible
            closeVideo(true)
            return true
        else if m.level = "menu" or m.level = "find"
            return false        ' leave the app
        else
            goBack()
            return true
        end if
    end if
    if key = "options" and (m.level = "menu" or m.level = "offline" or m.level = "loading")
        m.reg.Delete("server")
        m.reg.Flush()
        discover()
        return true
    end if
    if key = "OK" and m.level = "offline"
        load()
        return true
    end if
    if m.bar.visible and not m.video.visible
        if m.bar.hasFocus()
            if key = "OK"
                openSearch(m.lib)
                return true
            else if key = "down" and m.items.Count() > 0
                focusList()
                return true
            end if
        else if key = "up" and m.list.hasFocus() and m.list.itemFocused = 0
            focusBar()
            return true
        end if
        if key = "options"
            openSearch(m.lib)
            return true
        end if
    end if
    return false
end function
