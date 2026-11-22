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
    kb.title = "The PC's address, e.g. 192.168.1.20 (shown at the bottom of PLAY's window on the PC)"
    kb.buttons = ["OK", "Cancel"]
    kb.text = ""
    kb.observeField("buttonSelected", "onAddress")
    kb.observeField("wasClosed", "onDialogClosed")
    m.addrKb = kb
    m.top.dialog = kb
end sub

sub onAddress()
    t = m.addrKb.text.Trim()
    pressed = m.addrKb.buttonSelected
    m.addrKb.close = true
    if pressed <> 0 or t = "" then return
    url = serverUrl(t)
    if url = "" then return
    useServer(url)
end sub

' "192.168.1.20", "192.168.1.20:8700", "http://192.168.1.20/" or "https://…" -> "http://192.168.1.20:8600"
function serverUrl(t as string) as string
    t = t.Trim()
    low = LCase(t)
    if Left(low, 7) = "http://"
        t = Mid(t, 8)
    else if Left(low, 8) = "https://"
        t = Mid(t, 9)
    end if
    slash = Instr(1, t, "/")
    if slash > 0 then t = Left(t, slash - 1)
    if t = "" then return ""
    if Instr(1, t, ":") = 0 then t = t + ":8600"
    return "http://" + t
end function

' Back on a keyboard or dialog closes it without a button: give focus back to what was on screen.
sub onDialogClosed()
    if m.video.visible then return
    if m.level = "menu"
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
    v = invalid
    if m.task.result <> "ERROR" then v = ParseJson(m.task.result)
    if v = invalid or type(v) <> "roAssociativeArray" or v.movies = invalid
        m.level = "offline"
        m.status.text = "Can't reach PLAY at " + m.server + ". Is the PLAY program running on the PC?"
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
    if m.resume.Exists(it.url) then t = t + "   ·  resume at " + fmtTime(m.resume.Read(it.url).ToInt())
    return t
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
    kb.observeField("wasClosed", "onDialogClosed")
    m.kb = kb
    m.top.dialog = kb
end sub

sub onSearchButton()
    q = m.kb.text.Trim()
    pressed = m.kb.buttonSelected
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
    if m.resume.Exists(mv.url) then spot = m.resume.Read(mv.url).ToInt()
    if spot >= 30
        d = CreateObject("roSGNode", "Dialog")
        d.title = mv.title
        d.buttons = ["Resume from " + fmtTime(spot), "Start over"]
        d.observeField("buttonSelected", "onResumeButton")
        d.observeField("wasClosed", "onDialogClosed")
        m.resumeDlg = d
        m.resumePos = spot
        m.top.dialog = d
    else
        play(mv, 0)
    end if
end sub

sub onResumeButton()
    b = m.resumeDlg.buttonSelected
    m.resumeDlg.close = true
    if b = 0 then play(m.current, m.resumePos) else play(m.current, 0)
end sub

sub play(mv as object, start as integer)
    m.current = mv
    c = CreateObject("roSGNode", "ContentNode")
    c.url = m.server + mv.url
    fmt = mv.format
    if fmt = "mov" then fmt = "mp4"            ' Roku has no "mov" stream format; mp4 covers .mov and .m4v
    c.streamFormat = fmt
    c.title = mv.title
    if start > 0 then c.PlayStart = start
    m.video.content = c
    m.video.notificationInterval = 10
    m.lastSave = 0
    m.video.visible = true
    m.video.control = "play"
    m.video.setFocus(true)
end sub

' Resume points for files that were deleted or replaced are never asked for again; keep at most 300 so the
' channel's registry space (shared with the PC's address) never fills up.
sub trimResume()
    keys = m.resume.GetKeyList()
    if keys.Count() < 300 then return
    for i = 0 to 49
        m.resume.Delete(keys[i])
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
sub onPosition()
    if m.current = invalid then return
    p = Int(m.video.position)
    if p - m.lastSave >= 30 or m.lastSave - p >= 30
        m.lastSave = p
        savePos()
    end if
end sub

sub savePos()
    if m.current = invalid then return
    spot = Int(m.video.position)
    dur = m.video.duration
    if spot < 30 or (dur > 0 and spot > dur - 120)
        m.resume.Delete(m.current.url)
    else
        if not m.resume.Exists(m.current.url) then trimResume()
        m.resume.Write(m.current.url, spot.ToStr())
    end if
    m.resume.Flush()
end sub

sub onState()
    s = m.video.state
    if s = "error"
        savePos()
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
    if save then savePos()
    m.video.control = "stop"
    m.video.visible = false
    m.current = invalid
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
