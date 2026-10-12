Option Explicit
Dim databasePath, sql, provider, cn, rs, item, value, line, recordCount
databasePath = WScript.Arguments(0)
sql = WScript.Arguments(1)
For Each provider In Array("Microsoft.ACE.OLEDB.16.0", "Microsoft.ACE.OLEDB.12.0", "Microsoft.Jet.OLEDB.4.0")
  On Error Resume Next
  Set cn = CreateObject("ADODB.Connection")
  cn.Mode = 1
  cn.ConnectionTimeout = 10
  cn.CommandTimeout = 20
  cn.Open "Provider=" & provider & ";Data Source=" & databasePath & ";Mode=Read;Persist Security Info=False;"
  If Err.Number = 0 Then Exit For
  Err.Clear
  Set cn = Nothing
Next
If cn Is Nothing Then
  WScript.StdErr.WriteLine "READ_ONLY_ACCESS_PROVIDER_UNAVAILABLE"
  WScript.Quit 2
End If
On Error GoTo 0
Set rs = cn.Execute(sql)
line = ""
For item = 0 To rs.Fields.Count - 1
  If item > 0 Then line = line & ","
  line = line & CsvValue(rs.Fields(item).Name)
Next
WScript.StdOut.WriteLine line
recordCount = 0
Do Until rs.EOF
  line = ""
  For item = 0 To rs.Fields.Count - 1
    If item > 0 Then line = line & ","
    If IsNull(rs.Fields(item).Value) Then
      value = ""
    ElseIf rs.Fields(item).Type = 7 Or rs.Fields(item).Type = 135 Then
      value = IsoTime(rs.Fields(item).Value)
    Else
      value = CStr(rs.Fields(item).Value)
    End If
    line = line & CsvValue(value)
  Next
  WScript.StdOut.WriteLine line
  recordCount = recordCount + 1
  rs.MoveNext
Loop
rs.Close
cn.Close
WScript.StdOut.WriteLine "__MDB_QUERY_COMPLETE__," & recordCount

Function CsvValue(value)
  CsvValue = """" & Replace(Replace(CStr(value), """", """"""), vbCrLf, " ") & """"
End Function

Function IsoTime(value)
  IsoTime = Year(value) & "-" & Right("0" & Month(value), 2) & "-" & Right("0" & Day(value), 2) & " " & Right("0" & Hour(value), 2) & ":" & Right("0" & Minute(value), 2) & ":" & Right("0" & Second(value), 2)
End Function
