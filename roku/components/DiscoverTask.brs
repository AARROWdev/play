' Finds PLAY on the local network: broadcasts "PLAY-DISCOVER" to UDP 8601 and collects the replies for 2.5 s.
' result = JSON array of {"pc": name, "url": "http://<ip>:<port>"}
sub init()
    m.top.functionName = "go"
end sub

sub go()
    port = CreateObject("roMessagePort")
    sock = CreateObject("roDatagramSocket")
    sock.setMessagePort(port)
    addr = CreateObject("roSocketAddress")
    addr.setAddress("255.255.255.255:8601")
    sock.setSendToAddress(addr)
    sock.setBroadcast(true)
    sock.notifyReadable(true)
    found = {}
    out = []
    sent = 0
    clock = CreateObject("roTimespan")
    clock.mark()
    while clock.totalMilliseconds() < 2500
        if sent < 3 and clock.totalMilliseconds() >= sent * 700
            sock.sendStr("PLAY-DISCOVER")
            sent = sent + 1
        end if
        msg = wait(200, port)
        if type(msg) = "roSocketEvent" and sock.isReadable()
            data = sock.receiveStr(1024)
            from = sock.getReceivedFromAddress()
            info = ParseJson(data)
            if from <> invalid and isPlay(info)
                url = "http://" + from.getHostName() + ":" + info.port.ToStr()
                if not found.DoesExist(url)
                    found[url] = true
                    pc = "PC"
                    if isStr(info.pc) then pc = info.pc
                    out.Push({pc: pc, url: url})
                end if
            end if
        end if
    end while
    sock.close()
    m.top.result = FormatJson(out)
end sub

' A reply counts only if it is a JSON object with name "PLAY" and a whole-number port: anything else is ignored.
function isPlay(info as dynamic) as boolean
    if type(info) <> "roAssociativeArray" then return false
    if not isStr(info.name) then return false
    if info.name <> "PLAY" then return false
    t = type(info.port)
    return t = "roInt" or t = "roInteger" or t = "Integer" or t = "LongInteger" or t = "roLongInteger"
end function

function isStr(x as dynamic) as boolean
    t = type(x)
    return t = "roString" or t = "String"
end function
