$key = az functionapp keys list -n func-asrp-poc-sm01 -g rg-asrp-poc-app --query masterKey -o tsv
$H = @{ "x-functions-key" = $key; "Content-Type" = "application/json" }
$cats = @("Oct'24","Nov'24","Dec'24","Jan'25","Feb'25","Mar'25","Apr'25","May'25","Jun'25","Jul'25","Aug'25","Sep'25")
function Series($names) {
  $arr = @()
  foreach ($n in $names) {
    $vals = @(); for ($i=0; $i -lt $cats.Count; $i++) { $vals += (Get-Random -Min 1000 -Max 80000) }
    $arr += @{ name=$n; values=$vals }
  }
  return ,$arr
}
function Rows($prefix) {
  $locs = @("US","UK","HK","FR","SG")
  $arr = @()
  for ($i=1; $i -le 5; $i++) {
    $arr += @{ name="$prefix $i Ltd"; location=$locs[$i-1]; value=("{0:N0}" -f (Get-Random -Min 200 -Max 50000)) }
  }
  return ,$arr
}
function Mk($sid, $title, $prefix, $seriesNames) {
  return @{ slide_id=$sid; title=$title; customer_id="cust-alpine-industries"; content=@{ period="Oct 2024 - Sep 2025"; total=$null; chart_categories=$cats; chart_series=(Series $seriesNames); table_rows=(Rows $prefix); commentary="Currency mix is dominated by USD and EUR with seasonal Q1 peak." }; data_gaps=@() }
}
$slides = @(
  (Mk "volume_with_table_s33" "Priority Payments - Volume" "Beneficiary" @("USD","EUR","GBP")),
  (Mk "volume_with_table_s35" "Priority Payments - Value" "Remitter"    @("USD","EUR","GBP")),
  (Mk "volume_with_table_s36" "ACH - Volume"               "Beneficiary" @("USD","EUR","GBP")),
  (Mk "volume_with_table_s37" "ACH - Value"                "Remitter"    @("USD","EUR","GBP")),
  (Mk "volume_with_table_s38" "Direct Debits - Volume"     "Beneficiary" @("USD","EUR","GBP")),
  (Mk "volume_with_table_s39" "Direct Debits - Value"      "Remitter"    @("USD","EUR","GBP")),
  (Mk "volume_with_table_s40" "Real Time Payments - Volume" "Beneficiary" @("USD","EUR","GBP")),
  (Mk "volume_with_table_s41" "Real Time Payments - Value"  "Remitter"    @("USD","EUR","GBP"))
)
$body = @{ customer_id="cust-alpine-industries"; customer_name="Alpine Industries Ltd"; period="Oct 2024 - Sep 2025"; run_id="run-asm-" + ([guid]::NewGuid().ToString().Substring(0,8)); slides=$slides } | ConvertTo-Json -Depth 20
$asm = Invoke-WebRequest -Uri "https://func-asrp-poc-sm01.azurewebsites.net/api/deck/assemble" -Method POST -Headers $H -Body $body -UseBasicParsing -SkipHttpErrorCheck
"status=$($asm.StatusCode)"
$asm.Content
$url = ($asm.Content | ConvertFrom-Json).deck_blob_url
if ($url) {
  $stamp = Get-Date -Format "HHmmss"
  $out = ".\table8_$stamp.pptx"
  Invoke-WebRequest $url -OutFile $out -UseBasicParsing
  $tmp = ".\unzip_table8_$stamp"
  Remove-Item -Recurse -Force $tmp -ErrorAction SilentlyContinue
  Expand-Archive $out -DestinationPath $tmp
  "saved $out"
  foreach ($n in @(33,35,36,37,38,39,40,41)) {
    $cf = ([regex]::Match((Get-Content "$tmp\ppt\slides\_rels\slide${n}.xml.rels" -Raw), 'charts/(chart\d+\.xml)')).Groups[1].Value
    $cx = Get-Content "$tmp\ppt\charts\$cf" -Raw
    $serMatches = [regex]::Matches($cx, '<c:ser>[\s\S]*?</c:ser>')
    $sNames = @(); foreach ($m in $serMatches) { $tn = ([regex]::Match($m.Value, '<c:tx>[\s\S]*?<c:v>([^<]+)</c:v>')).Groups[1].Value; $sNames += $tn }
    $valBlk = ([regex]::Match($serMatches[0].Value, '<c:val>[\s\S]*?</c:val>')).Value
    $val0 = ([regex]::Matches($valBlk, '<c:v>([^<]+)</c:v>') | Select-Object -First 3 | ForEach-Object { $_.Groups[1].Value }) -join ','
    $sx = Get-Content "$tmp\ppt\slides\slide${n}.xml" -Raw
    $tbl = ([regex]::Match($sx, '<a:tbl>[\s\S]*?</a:tbl>')).Value
    $rows = [regex]::Matches($tbl, '<a:tr[\s\S]*?</a:tr>')
    # data row 1 (index 1, second row)
    $cells = [regex]::Matches($rows[1].Value, '<a:tc[\s\S]*?</a:tc>')
    $row1 = @(); foreach ($c in $cells) { $row1 += (([regex]::Matches($c.Value, '<a:t>([^<]*)</a:t>') | ForEach-Object { $_.Groups[1].Value }) -join '') }
    $tokenLeft = if ($sx -match '\[\s\s\]') { 'YES' } else { 'NO' }
    "S${n}: chart=$cf series=[$($sNames -join ',')] vals0=$val0 row1=[$($row1 -join '|')] token_left=$tokenLeft"
  }
}
