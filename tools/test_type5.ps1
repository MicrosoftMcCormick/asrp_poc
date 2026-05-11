$key = az functionapp keys list -n func-asrp-poc-sm01 -g rg-asrp-poc-app --query masterKey -o tsv
$H = @{ "x-functions-key" = $key; "Content-Type" = "application/json" }
$cats = @("Oct'24","Nov'24","Dec'24","Jan'25","Feb'25","Mar'25","Apr'25","May'25","Jun'25","Jul'25","Aug'25","Sep'25")
function MkType($sid, $title, $seriesNames) {
  $series = @()
  $total = 0
  foreach ($n in $seriesNames) {
    $vals = @()
    for ($i=0; $i -lt $cats.Count; $i++) { $v = Get-Random -Min 20 -Max 200; $vals += $v; $total += $v }
    $series += @{ name = $n; values = $vals }
  }
  return @{ slide_id=$sid; title=$title; customer_id="cust-alpine-industries"; content=@{ period="Oct 2024 - Sep 2025"; total=$total; categories=$cats; series=$series; commentary="Volumes remained broadly stable across the period with seasonal peaks in Q1." }; data_gaps=@() }
}
$slides = @(
  (MkType "volume_by_type_s28" "Service Cases by Type" @("Investigation","Amendment","Compliance","Other")),
  (MkType "volume_by_type_s31" "Investigation Sub-types" @("Status / Trace","NSF","Additional Details","Compliance / OFAC")),
  (MkType "volume_by_type_s32" "Amendment Volume" @("Beneficiary","Reference")),
  (MkType "volume_by_type_s34" "Compliance Volume" @("RFI","Sanctions")),
  (MkType "volume_by_type_s42" "Cheque Detail" @("Issued","Returned"))
)
$body = @{ customer_id="cust-alpine-industries"; customer_name="Alpine Industries Ltd"; period="Oct 2024 - Sep 2025"; run_id="run-asm-" + ([guid]::NewGuid().ToString().Substring(0,8)); slides=$slides } | ConvertTo-Json -Depth 20
$asm = Invoke-WebRequest -Uri "https://func-asrp-poc-sm01.azurewebsites.net/api/deck/assemble" -Method POST -Headers $H -Body $body -UseBasicParsing -SkipHttpErrorCheck
"status=$($asm.StatusCode)"
$asm.Content
$url = ($asm.Content | ConvertFrom-Json).deck_blob_url
if ($url) {
  $stamp = Get-Date -Format "HHmmss"
  $out = ".\type5_$stamp.pptx"
  Invoke-WebRequest $url -OutFile $out -UseBasicParsing
  $tmp = ".\unzip_type5_$stamp"
  Remove-Item -Recurse -Force $tmp -ErrorAction SilentlyContinue
  Expand-Archive $out -DestinationPath $tmp
  "saved $out"
  foreach ($n in @(28,31,32,34,42)) {
    $cf = ([regex]::Match((Get-Content "$tmp\ppt\slides\_rels\slide${n}.xml.rels" -Raw), 'charts/(chart\d+\.xml)')).Groups[1].Value
    $cx = Get-Content "$tmp\ppt\charts\$cf" -Raw
    $serMatches = [regex]::Matches($cx, '<c:ser>[\s\S]*?</c:ser>')
    $serCount = $serMatches.Count
    $first = if ($serCount -gt 0) {
      $valBlk = ([regex]::Match($serMatches[0].Value, '<c:val>[\s\S]*?</c:val>')).Value
      (([regex]::Matches($valBlk, '<c:v>([^<]+)</c:v>')) | Select-Object -First 4 | ForEach-Object { $_.Groups[1].Value }) -join ','
    } else { '' }
    $catBlk = if ($serCount -gt 0) { ([regex]::Match($serMatches[0].Value, '<c:cat>[\s\S]*?</c:cat>')).Value } else { '' }
    $firstCat = ([regex]::Matches($catBlk, '<c:v>([^<]+)</c:v>') | Select-Object -First 3 | ForEach-Object { $_.Groups[1].Value }) -join ','
    $serName0 = if ($serCount -gt 0) {
      $txBlk = ([regex]::Match($serMatches[0].Value, '<c:tx>[\s\S]*?</c:tx>')).Value
      ([regex]::Match($txBlk, '<c:v>([^<]+)</c:v>')).Groups[1].Value
    } else { '' }
    $tx = Get-Content "$tmp\ppt\slides\slide${n}.xml" -Raw
    $tokenLeft = if ($tx -match '\[\s\s\]') { 'YES' } else { 'NO' }
    "S${n}: chart=$cf ser_count=$serCount cats=$firstCat ser0_name=$serName0 ser0_vals=$first token_left=$tokenLeft"
  }
}
