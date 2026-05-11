$key = az functionapp keys list -n func-asrp-poc-sm01 -g rg-asrp-poc-app --query masterKey -o tsv
$H = @{ "x-functions-key" = $key; "Content-Type" = "application/json" }
function Slice($countries) {
  $arr = @()
  foreach ($c in $countries) { $arr += @{ country = $c; count = (Get-Random -Min 20 -Max 400) } }
  return ,$arr
}
function MkChan($name, $countries) {
  return @{ name = $name; by_country = (Slice $countries) }
}
$cn = @("HK","SG","FR","DE","UKRFB")
$slides = @(
  @{ slide_id="channel_mix_s46"; title="Transactions by Channel Type"; customer_id="cust-alpine-industries"; content=@{ period="Oct 2024 - Sep 2025"; total=$null; channels=@((MkChan "SWIFT" $cn), (MkChan "FLU" $cn)); commentary="SWIFT remains the dominant outward channel with HK and SG leading volumes." }; data_gaps=@() },
  @{ slide_id="channel_mix_s47"; title="Transactions by Channel Type"; customer_id="cust-alpine-industries"; content=@{ period="Oct 2024 - Sep 2025"; total=$null; channels=@((MkChan "H2H" $cn), (MkChan "HSBCnet" $cn)); commentary="H2H and HSBCnet show steady cross-region usage led by HK and SG." }; data_gaps=@() },
  @{ slide_id="channel_mix_s48"; title="Transactions by Channel Type"; customer_id="cust-alpine-industries"; content=@{ period="Oct 2024 - Sep 2025"; total=$null; channels=@((MkChan "API" $cn)); commentary="API channel adoption is concentrated in HK with growing SG share." }; data_gaps=@() }
)
$body = @{ customer_id="cust-alpine-industries"; customer_name="Alpine Industries Ltd"; period="Oct 2024 - Sep 2025"; run_id="run-asm-" + ([guid]::NewGuid().ToString().Substring(0,8)); slides=$slides } | ConvertTo-Json -Depth 20
$asm = Invoke-WebRequest -Uri "https://func-asrp-poc-sm01.azurewebsites.net/api/deck/assemble" -Method POST -Headers $H -Body $body -UseBasicParsing -SkipHttpErrorCheck
"status=$($asm.StatusCode)"
$asm.Content
$url = ($asm.Content | ConvertFrom-Json).deck_blob_url
if ($url) {
  $stamp = Get-Date -Format "HHmmss"
  $out = ".\channel3_$stamp.pptx"
  Invoke-WebRequest $url -OutFile $out -UseBasicParsing
  $tmp = ".\unzip_channel3_$stamp"
  Remove-Item -Recurse -Force $tmp -ErrorAction SilentlyContinue
  Expand-Archive $out -DestinationPath $tmp
  "saved $out"
  foreach ($n in @(46,47,48)) {
    $charts = [regex]::Matches((Get-Content "$tmp\ppt\slides\_rels\slide${n}.xml.rels" -Raw), 'charts/(chart\d+\.xml)') | ForEach-Object { $_.Groups[1].Value }
    foreach ($cf in $charts) {
      $cx = Get-Content "$tmp\ppt\charts\$cf" -Raw
      $serBlk = ([regex]::Match($cx, '<c:ser>[\s\S]*?</c:ser>')).Value
      $name = ([regex]::Match($serBlk, '<c:tx>[\s\S]*?<c:v>([^<]+)</c:v>')).Groups[1].Value
      $valBlk = ([regex]::Match($serBlk, '<c:val>[\s\S]*?</c:val>')).Value
      $vals = ([regex]::Matches($valBlk, '<c:v>([^<]+)</c:v>') | ForEach-Object { $_.Groups[1].Value }) -join ','
      $catBlk = ([regex]::Match($serBlk, '<c:cat>[\s\S]*?</c:cat>')).Value
      $cats = ([regex]::Matches($catBlk, '<c:v>([^<]+)</c:v>') | ForEach-Object { $_.Groups[1].Value }) -join ','
      "S${n} $cf name='$name' cats=$cats vals=$vals"
    }
    $tx = Get-Content "$tmp\ppt\slides\slide${n}.xml" -Raw
    $tokenLeft = if ($tx -match '\[\s\s\]') { 'YES' } else { 'NO' }
    "S${n} token_left=$tokenLeft"
  }
}
