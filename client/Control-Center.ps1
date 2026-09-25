#Requires -Version 5.1
param([string]$ConfigPath=(Join-Path $env:LOCALAPPDATA 'Homelab\client-settings.json'))
$ErrorActionPreference='Stop'
. (Join-Path $PSScriptRoot 'Client.Common.ps1')
$config=Read-HomelabClientConfig $ConfigPath
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
[Windows.Forms.Application]::EnableVisualStyles()
$form=New-Object Windows.Forms.Form
$form.Text="Homelab - $($config.Role)"
$form.Size=New-Object Drawing.Size(940,720)
$form.MinimumSize=New-Object Drawing.Size(800,600)
$form.StartPosition='CenterScreen'
$form.Font=New-Object Drawing.Font('Segoe UI',10)
$form.BackColor=[Drawing.Color]::FromArgb(242,246,247)
$layout=New-Object Windows.Forms.TableLayoutPanel
$layout.Dock='Fill';$layout.Padding=New-Object Windows.Forms.Padding(22);$layout.ColumnCount=1;$layout.RowCount=6
foreach($height in @(55,55,45,100,60,90)){$style=New-Object Windows.Forms.RowStyle; if($height -eq 100){$style.SizeType='Percent';$style.Height=100}else{$style.SizeType='Absolute';$style.Height=$height};[void]$layout.RowStyles.Add($style)}
$form.Controls.Add($layout)
$title=New-Object Windows.Forms.Label
$title.Text='Your models and machines';$title.Font=New-Object Drawing.Font('Segoe UI',22);$title.Dock='Fill';$layout.Controls.Add($title,0,0)
$links=New-Object Windows.Forms.FlowLayoutPanel;$links.Dock='Fill';$layout.Controls.Add($links,0,1)
$tools=New-Object Windows.Forms.FlowLayoutPanel;$tools.Dock='Fill';$layout.Controls.Add($tools,0,2)
$grid=New-Object Windows.Forms.DataGridView
$grid.Dock='Fill';$grid.ReadOnly=$true;$grid.AllowUserToAddRows=$false;$grid.AllowUserToDeleteRows=$false;$grid.MultiSelect=$false;$grid.SelectionMode='FullRowSelect';$grid.AutoSizeColumnsMode='Fill';$grid.RowHeadersVisible=$false;$grid.BackgroundColor=[Drawing.Color]::White
foreach($column in @('File','Size (GiB)','Format')){[void]$grid.Columns.Add($column,$column)}
$layout.Controls.Add($grid,0,3)
$modelActions=New-Object Windows.Forms.FlowLayoutPanel;$modelActions.Dock='Fill';$layout.Controls.Add($modelActions,0,4)
$steam=New-Object Windows.Forms.FlowLayoutPanel;$steam.Dock='Fill';$layout.Controls.Add($steam,0,5)
$script:hpToken=''
function Show-Error($Message){[void][Windows.Forms.MessageBox]::Show([string]$Message,'Homelab','OK','Error')}
function Add-Button($Panel,[string]$Text,[scriptblock]$Click){$b=New-Object Windows.Forms.Button;$b.Text=$Text;$b.AutoSize=$true;$b.Padding=New-Object Windows.Forms.Padding(8);$b.Margin=New-Object Windows.Forms.Padding(0,4,10,4);$b.Add_Click($Click);$Panel.Controls.Add($b);return $b}
function Get-Token {
    $dialog=New-Object Windows.Forms.Form;$dialog.Text='HP login token';$dialog.Size=New-Object Drawing.Size(520,180);$dialog.StartPosition='CenterParent'
    $label=New-Object Windows.Forms.Label;$label.Text='Paste the HP app token. It stays in memory until you close this app.';$label.Location=New-Object Drawing.Point(15,15);$label.AutoSize=$true;$dialog.Controls.Add($label)
    $box=New-Object Windows.Forms.TextBox;$box.UseSystemPasswordChar=$true;$box.Location=New-Object Drawing.Point(15,45);$box.Width=470;$dialog.Controls.Add($box)
    $ok=New-Object Windows.Forms.Button;$ok.Text='Connect';$ok.Location=New-Object Drawing.Point(385,85);$ok.DialogResult='OK';$dialog.Controls.Add($ok);$dialog.AcceptButton=$ok
    if($dialog.ShowDialog($form) -eq 'OK'){$script:hpToken=$box.Text};$dialog.Dispose()
}
function Selected-Name {if($grid.SelectedRows.Count -ne 1){throw 'Select one model first.'};$name=[string]$grid.SelectedRows[0].Cells[0].Value;Assert-HomelabModelName $name;return $name}
function Start-Action([string]$Action,[string]$Name='') {
    $arguments='-NoProfile -NoExit -ExecutionPolicy Bypass -File '+(Quote-HomelabArgument (Join-Path $PSScriptRoot 'Invoke-ClientAction.ps1'))+' -Action '+$Action+' -ConfigPath '+(Quote-HomelabArgument $ConfigPath)
    if($Name){$arguments+=' -Name '+(Quote-HomelabArgument $Name)}
    $start=@{FilePath='powershell.exe';ArgumentList=$arguments}
    if($Action -in @('Link','Attach','Release','Status')){$start.Verb='RunAs'}
    Start-Process @start | Out-Null
}
[void](Add-Button $links 'HP model library' {Start-Process $config.HpUrl})
[void](Add-Button $links 'Dell projects and jobs' {Start-Process $config.DellUrl})
[void](Add-Button $links 'Open Models share' {Start-Process explorer.exe -ArgumentList (Quote-HomelabArgument $config.ModelsShare)})
[void](Add-Button $links 'Setup guide' {Start-Process (Join-Path $config.RepoRoot 'docs\guide.html')})
[void](Add-Button $tools 'Refresh model list' {
    try {if(-not $script:hpToken){Get-Token};if(-not $script:hpToken){return}
        $models=Invoke-RestMethod ($config.HpUrl+'/api/models') -Headers @{Authorization='Bearer '+$script:hpToken} -TimeoutSec 30
        $grid.Rows.Clear();foreach($m in $models.items){[void]$grid.Rows.Add([string]$m.id,([math]::Round($m.size_bytes/1GB,2)),[string]$m.format)}
    }catch{$script:hpToken='';Show-Error $_.Exception.Message}
})
[void](Add-Button $tools 'Change HP token' {Get-Token})
$linkButton=Add-Button $modelActions 'Link selected model from NAS' {try{Start-Action 'Link' (Selected-Name)}catch{Show-Error $_.Exception.Message}}
$loadButton=Add-Button $modelActions 'Load selected model' {try{$name=Selected-Name;if([Windows.Forms.MessageBox]::Show("Load $name through Switchboard? This may replace the currently loaded model.",'Load model','YesNo','Question') -eq 'Yes'){Start-Action 'Load' $name}}catch{Show-Error $_.Exception.Message}}
$linkButton.Enabled=($config.Role -eq 'laptop');$loadButton.Enabled=$linkButton.Enabled
[void](Add-Button $steam 'Steam drive: status' {try{Start-Action 'Status'}catch{Show-Error $_.Exception.Message}})
[void](Add-Button $steam 'Attach game drive here' {try{Start-Action 'Attach'}catch{Show-Error $_.Exception.Message}})
[void](Add-Button $steam 'Release game drive' {try{Start-Action 'Release'}catch{Show-Error $_.Exception.Message}})
$note=New-Object Windows.Forms.Label;$note.Text='Close Steam and games before releasing. The command window shows the result and any required confirmation.';$note.AutoSize=$true;$steam.SetFlowBreak($steam.Controls[$steam.Controls.Count-1],$true);$steam.Controls.Add($note)
[void]$form.ShowDialog()
$script:hpToken=''
