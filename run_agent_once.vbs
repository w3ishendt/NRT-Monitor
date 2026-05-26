Set shell = CreateObject("WScript.Shell")
Set fileSystem = CreateObject("Scripting.FileSystemObject")
scriptDirectory = fileSystem.GetParentFolderName(WScript.ScriptFullName)
pythonPath = fileSystem.BuildPath(scriptDirectory, "venv\Scripts\python.exe")
If Not fileSystem.FileExists(pythonPath) Then
	pythonPath = fileSystem.BuildPath(scriptDirectory, "venv\Scripts\pythonw.exe")
End If
agentPath = fileSystem.BuildPath(scriptDirectory, "agent.py")
shell.CurrentDirectory = scriptDirectory
quote = Chr(34)
launchCommand = quote & pythonPath & quote & " " & quote & agentPath & quote
shell.Run launchCommand, 2, False
