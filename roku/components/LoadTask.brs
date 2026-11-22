' Fetches one URL. result is the body, or "ERROR" when the PC can't be reached, answers with an error, or takes
' longer than 8 s. The field is alwaysNotify, so the scene hears every answer, even the same one twice.
sub init()
    m.top.functionName = "go"
end sub

sub go()
    port = CreateObject("roMessagePort")
    t = CreateObject("roUrlTransfer")
    t.SetMessagePort(port)
    t.SetUrl(m.top.url)
    body = "ERROR"
    if t.AsyncGetToString()
        msg = wait(8000, port)
        if type(msg) = "roUrlEvent"
            if msg.GetResponseCode() = 200 then body = msg.GetString()
        else
            t.AsyncCancel()
        end if
    end if
    m.top.result = body
end sub
