$key = az functionapp keys list -n func-asrp-poc-sm01 -g rg-asrp-poc-app --query masterKey -o tsv
$H = @{ "x-functions-key" = $key; "Content-Type" = "application/json" }
$cats = @("Oct'24","Nov'24","Dec'24","Jan'25","Feb'25","Mar'25","Apr'25","May'25","Jun'25","Jul'25","Aug'25","Sep'25")
function Vals($min, $max) { $a = @(); for ($i=0; $i -lt $cats.Count; $i++) { $a += (Get-Random -Min $min -Max $max) }; return ,$a }
$slides = @(
  @{
    slide_id="case_subtype_s22"; title="Cases by Sub-type"; customer_id="cust-alpine-industries"
    content=@{ period="Oct 2024 - Sep 2025"; total=$null
      by_subtype=@(
        @{name="Status / Trace"; count=312}
        @{name="NSF / Limit Enquiry"; count=204}
        @{name="Additional Details"; count=158}
        @{name="Compliance / OFAC / RFI"; count=121}
        @{name="Return of Funds"; count=87}
        @{name="Amend / Recall / Cancel"; count=64}
      )
      commentary="Status / Trace remains the dominant sub-type, accounting for over a third of cases."
    }; data_gaps=@()
  },
  @{
    slide_id="multi_chart_trend_s20"; title="Service Queries"; customer_id="cust-alpine-industries"
    content=@{ period="Oct 2024 - Sep 2025"
      charts=@(
        @{ categories=$cats; series=@(
          @{name="Opened";       values=(Vals 80 200)}
          @{name="Resolved";     values=(Vals 80 200)}
          @{name="Average TAT";  values=(Vals 2 8)}
        )}
      )
      commentary="Opened and resolved volumes track closely with TAT averaging around five days."
    }; data_gaps=@()
  },
  @{
    slide_id="multi_chart_trend_s25"; title="STP and Non-STP"; customer_id="cust-alpine-industries"
    content=@{ period="Oct 2024 - Sep 2025"
      charts=@(
        @{ categories=$cats; series=@(
          @{name="STP Instructions";            values=(Vals 30000 60000)}
          @{name="Repaired (Non-STP)";          values=(Vals 1000 5000)}
          @{name="STP %";                       values=(Vals 88 97)}
        )}
        @{ categories=$cats; series=@(
          @{name="RFI";   values=(Vals 50 200)}
          @{name="RFI%";  values=(Vals 1 6)}
        )}
      )
      commentary="STP rates remain consistently above 90 per cent with RFI volumes broadly stable."
    }; data_gaps=@()
  },
  @{
    slide_id="multi_chart_trend_s45"; title="Channel Mix Trend"; customer_id="cust-alpine-industries"
    content=@{ period="Oct 2024 - Sep 2025"
      charts=@(
        @{ categories=$cats; series=@(
          @{name="SWIFT";              values=(Vals 1000 5000)}
          @{name="H2H";                values=(Vals 500 3000)}
          @{name="FLU";                values=(Vals 200 1500)}
          @{name="HSBCnet On Screen";  values=(Vals 100 800)}
          @{name="API";                values=(Vals 50 600)}
        )}
        @{ categories=$cats; series=@(
          @{name="SWIFT ";             values=(Vals 100000 500000)}
          @{name="H2H";                values=(Vals 50000 300000)}
          @{name="FLU";                values=(Vals 20000 150000)}
          @{name="HSBCnet on Screen";  values=(Vals 10000 80000)}
          @{name="API";                values=(Vals 5000 60000)}
        )}
      )
      commentary="SWIFT continues to dominate both volume and value across all months."
    }; data_gaps=@()
  }
)
$body = @{ customer_id="cust-alpine-industries"; customer_name="Alpine Industries Ltd"; period="Oct 2024 - Sep 2025"; run_id="run-asm-" + ([guid]::NewGuid().ToString().Substring(0,8)); slides=$slides } | ConvertTo-Json -Depth 30
$asm = Invoke-WebRequest -Uri "https://func-asrp-poc-sm01.azurewebsites.net/api/deck/assemble" -Method POST -Headers $H -Body $body -UseBasicParsing -SkipHttpErrorCheck
"status=$($asm.StatusCode)"
$asm.Content
$url = ($asm.Content | ConvertFrom-Json).deck_blob_url
if ($url) {
  $stamp = Get-Date -Format "HHmmss"
  $out = ".\final4_$stamp.pptx"
  Invoke-WebRequest $url -OutFile $out -UseBasicParsing
  $tmp = ".\unzip_final4_$stamp"
  Remove-Item -Recurse -Force $tmp -ErrorAction SilentlyContinue
  Expand-Archive $out -DestinationPath $tmp
  "saved $out"
  foreach ($n in @(20,22,25,45)) {
    $charts = ([regex]::Matches((Get-Content "$tmp\ppt\slides\_rels\slide${n}.xml.rels" -Raw), 'charts/(chart\d+\.xml)') | ForEach-Object { $_.Groups[1].Value })
    foreach ($cf in $charts) {
      $cx = Get-Content "$tmp\ppt\charts\$cf" -Raw
      $sers = [regex]::Matches($cx, '<c:ser>[\s\S]*?</c:ser>')
      $names = @(); foreach ($s in $sers) { $names += ([regex]::Match($s.Value, '<c:tx>[\s\S]*?<c:v>([^<]+)</c:v>')).Groups[1].Value }
      $val0 = ([regex]::Matches([regex]::Match($sers[0].Value, '<c:val>[\s\S]*?</c:val>').Value, '<c:v>([^<]+)</c:v>') | Select-Object -First 4 | ForEach-Object { $_.Groups[1].Value }) -join ','
      "  S${n} $cf series=[$($names -join ' | ')] vals0=$val0"
    }
    $sx = Get-Content "$tmp\ppt\slides\slide${n}.xml" -Raw
    $tokenLeft = if ($sx -match '\[\s\s\]') { 'YES' } else { 'NO' }
    "S${n} token_left=$tokenLeft"
  }
}
