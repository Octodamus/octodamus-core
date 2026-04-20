import subprocess

xml = (
    '<?xml version="1.0" encoding="UTF-16"?>\n'
    '<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">\n'
    '  <Triggers>\n'
    '    <BootTrigger><Enabled>true</Enabled></BootTrigger>\n'
    '  </Triggers>\n'
    '  <Settings>\n'
    '    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>\n'
    '    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>\n'
    '    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>\n'
    '    <ExecutionTimeLimit>PT0S</ExecutionTimeLimit>\n'
    '    <Enabled>true</Enabled>\n'
    '  </Settings>\n'
    '  <Actions>\n'
    '    <Exec>\n'
    '      <Command>C:\\Python314\\python.exe</Command>\n'
    '      <Arguments>C:\\Users\\walli\\octodamus\\octo_boto_botcoin.py --loop</Arguments>\n'
    '      <WorkingDirectory>C:\\Users\\walli\\octodamus</WorkingDirectory>\n'
    '    </Exec>\n'
    '  </Actions>\n'
    '</Task>\n'
)

xml_path = r'C:\Users\walli\octodamus\botcoin_miner_task.xml'
with open(xml_path, 'w', encoding='utf-16') as f:
    f.write(xml)

result = subprocess.run(
    ['powershell', '-Command',
     'schtasks /create /tn "Octodamus-BOTCOIN-Miner" /xml "' + xml_path + '" /f'],
    capture_output=True, text=True
)
print(result.stdout)
if result.stderr:
    print('ERR:', result.stderr)
