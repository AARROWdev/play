sub init()
    m.top.functionName = "go"
end sub

sub go()
    t = CreateObject("roUrlTransfer")
    t.SetUrl(m.top.url)
    t.SetConnectionTimeout(5000)
    m.top.result = t.GetToString()
end sub
