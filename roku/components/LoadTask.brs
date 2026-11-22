' Fetches one URL. result is the body on HTTP 200, otherwise "ERROR:<code>": the HTTP status (403 refused, 503 PLAY
' is still reading its folders), or 0 when the PC can't be reached or takes longer than 15 s. The field is
' alwaysNotify, so the scene hears every answer, even the same one twice.
sub init()
    m.top.functionName = "go"
end sub

sub go()
    port = CreateObject("roMessagePort")
    t = CreateObject("roUrlTransfer")
    t.SetMessagePort(port)
    t.SetUrl(m.top.url)
    body = "ERROR:0"
    if t.AsyncGetToString()
        msg = wait(15000, port)
        if type(msg) = "roUrlEvent"
            code = msg.GetResponseCode()
            if code = 200
                body = msg.GetString()
            else if code > 0
                body = "ERROR:" + code.ToStr()
            end if
        else
            t.AsyncCancel()
        end if
    end if
    m.top.result = body
end sub
